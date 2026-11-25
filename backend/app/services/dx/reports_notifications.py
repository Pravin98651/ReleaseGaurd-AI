"""Release Readiness Reports, Shareable Reports, and Slack/Discord Notifications (Phase 6)."""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.core.security import (
    decrypt_token,
    encrypt_token,
    make_token_hint,
    scrub_secrets,
)
from app.models import (
    Approval,
    ApprovalStatus,
    DeliveryStatus,
    IntegrationAuthMode,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    NotificationDelivery,
    ProgressiveRollout,
    Release,
    ReleaseReadinessReport,
    ReleaseReadinessStatus,
    ReleaseRiskAssessment,
    ReportReadinessOutcome,
    Repository,
    RepositoryIntelligenceProfile,
    ShareableReport,
    ShareLinkAccessLog,
)
from app.services.dx.github_dx import DXIntegrationError
from app.services.release_readiness import ReleaseReadinessService

logger = logging.getLogger("releaseguard.dx.reports")


class ReleaseReadinessReportService:
    """Generates authoritative readiness reports from ReleaseReadinessService + Phase 3-5 data."""

    @staticmethod
    async def generate_report(
        session: AsyncSession,
        release: Release,
        *,
        created_by_id: uuid.UUID | None = None,
    ) -> ReleaseReadinessReport:
        readiness = await ReleaseReadinessService.evaluate_release(
            session, release, actor_id=created_by_id, emit_event=False
        )
        repo = await session.get(Repository, release.repository_id) if release.repository_id else None
        risk = (
            await session.execute(
                select(ReleaseRiskAssessment)
                .where(
                    ReleaseRiskAssessment.release_id == release.id,
                    ReleaseRiskAssessment.is_current.is_(True),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        rollout = (
            await session.execute(
                select(ProgressiveRollout)
                .where(ProgressiveRollout.release_id == release.id)
                .order_by(ProgressiveRollout.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        approvals = list(
            (await session.execute(select(Approval).where(Approval.release_id == release.id)))
            .scalars()
            .all()
        )
        repo_profile = (
            await session.execute(
                select(RepositoryIntelligenceProfile)
                .where(RepositoryIntelligenceProfile.repository_id == release.repository_id)
                .limit(1)
            )
            if release.repository_id
            else None
        )
        prof = repo_profile.scalar_one_or_none() if repo_profile is not None else None

        pending_apps = [a for a in approvals if a.status == ApprovalStatus.PENDING]
        blockers = readiness.get("blockers") or []
        warnings = readiness.get("warnings") or []

        if pending_apps:
            outcome = ReportReadinessOutcome.AWAITING_APPROVAL
        elif blockers or release.readiness_status == ReleaseReadinessStatus.BLOCKED:
            outcome = ReportReadinessOutcome.BLOCKED
        elif release.readiness_status == ReleaseReadinessStatus.NOT_READY:
            outcome = ReportReadinessOutcome.INSUFFICIENT_EVIDENCE
        elif warnings:
            outcome = ReportReadinessOutcome.READY_WITH_WARNINGS
        else:
            outcome = ReportReadinessOutcome.READY

        report_data = {
            "release": {
                "id": str(release.id),
                "release_key": release.release_key,
                "title": release.title,
                "environment": release.target_environment,
                "status": release.status.value,
                "repository": repo.full_name if repo else None,
            },
            "readiness": {
                "outcome": outcome.value,
                "score": release.readiness_score,
                "status": release.readiness_status.value,
                "blockers": blockers,
                "warnings": warnings,
            },
            "risk_assessment": {
                "risk_score": risk.risk_score if risk else None,
                "risk_level": risk.risk_level.value if risk else release.risk_level.value,
                "flag_suitability": risk.flag_suitability.value if risk else None,
            },
            "progressive_delivery": {
                "rollout_key": rollout.rollout_key if rollout else None,
                "status": rollout.status.value if rollout else None,
                "current_percentage": rollout.current_percentage if rollout else None,
            },
            "approvals": {
                "total": len(approvals),
                "pending": len(pending_apps),
            },
            "historical_context": {
                "repository_release_count": prof.release_count if prof else 0,
                "repository_failure_rate": prof.historical_failure_rate if prof else None,
            },
        }

        md_lines = [
            f"# Release Readiness Report: {release.release_key} — {release.title}",
            "",
            f"- **Environment:** `{release.target_environment}`",
            f"- **Readiness Outcome:** `{outcome.value.upper()}` (Score: `{release.readiness_score}/100`)",
            f"- **Risk Level:** `{report_data['risk_assessment']['risk_level']}`",
            f"- **Pending Approvals:** `{len(pending_apps)}`",
            "",
            "## Blockers",
            *([f"- {b}" for b in blockers] if blockers else ["- None"]),
            "",
            "## Warnings",
            *([f"- {w}" for w in warnings] if warnings else ["- None"]),
        ]
        report_key = f"RPT-{release.release_key}-{str(uuid.uuid4())[:6].upper()}"
        report = ReleaseReadinessReport(
            release_id=release.id,
            report_key=report_key,
            title=f"Readiness Report: {release.release_key}",
            readiness_outcome=outcome,
            report_data=report_data,
            markdown_content=scrub_secrets("\n".join(md_lines)),
            created_by_id=created_by_id,
        )
        session.add(report)
        await session.flush()
        return report


class ShareableReportService:
    """Manages cryptographically secure, expirable, revocable share links."""

    @staticmethod
    def _hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @staticmethod
    async def create_share_link(
        session: AsyncSession,
        *,
        report: ReleaseReadinessReport,
        created_by_id: uuid.UUID | None = None,
        expires_in_hours: int = 24,
    ) -> tuple[ShareableReport, str]:
        raw_token = secrets.token_urlsafe(32)
        share = ShareableReport(
            report_id=report.id,
            token_hash=ShareableReportService._hash_token(raw_token),
            created_by_id=created_by_id,
            expires_at=utcnow() + timedelta(hours=max(1, min(expires_in_hours, 720))),
        )
        session.add(share)
        await session.flush()
        return share, raw_token

    @staticmethod
    async def revoke_share_link(session: AsyncSession, share: ShareableReport) -> ShareableReport:
        share.revoked_at = utcnow()
        await session.flush()
        return share

    @staticmethod
    async def resolve_shared_report(
        session: AsyncSession,
        *,
        raw_token: str,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> ReleaseReadinessReport:
        token_hash = ShareableReportService._hash_token(raw_token)
        share = (
            await session.execute(
                select(ShareableReport).where(ShareableReport.token_hash == token_hash).limit(1)
            )
        ).scalar_one_or_none()
        if share is None:
            raise DXIntegrationError("Share link not found.", status_code=404)

        now = utcnow()
        if share.revoked_at is not None:
            session.add(
                ShareLinkAccessLog(
                    share_id=share.id, client_ip=client_ip, user_agent=user_agent, outcome="revoked"
                )
            )
            await session.flush()
            raise DXIntegrationError("Share link has been revoked.", status_code=410)

        exp = share.expires_at
        if exp.tzinfo is None:
            from datetime import UTC

            exp = exp.replace(tzinfo=UTC)
        if now >= exp:
            session.add(
                ShareLinkAccessLog(
                    share_id=share.id, client_ip=client_ip, user_agent=user_agent, outcome="expired"
                )
            )
            await session.flush()
            raise DXIntegrationError("Share link has expired.", status_code=410)

        report = await session.get(ReleaseReadinessReport, share.report_id)
        if report is None:
            raise DXIntegrationError("Report not found.", status_code=404)

        share.access_count += 1
        share.last_accessed_at = now
        session.add(
            ShareLinkAccessLog(
                share_id=share.id, client_ip=client_ip, user_agent=user_agent, outcome="granted"
            )
        )
        await session.flush()
        return report


class DeveloperNotificationService:
    """Manages Slack & Discord webhook integrations and tracks NotificationDelivery."""

    @staticmethod
    async def connect_provider(
        session: AsyncSession,
        *,
        provider: IntegrationProvider,
        webhook_url: str,
        channel_label: str = "#releases",
        connected_by_id: uuid.UUID | None = None,
    ) -> IntegrationConnection:
        if provider not in (IntegrationProvider.SLACK, IntegrationProvider.DISCORD):
            raise DXIntegrationError("Unsupported notification provider.")
        if not webhook_url.startswith("https://"):
            raise DXIntegrationError("Webhook URL must use HTTPS.")

        existing = (
            await session.execute(
                select(IntegrationConnection)
                .where(IntegrationConnection.provider == provider)
                .limit(1)
            )
        ).scalar_one_or_none()

        enc = encrypt_token(webhook_url)
        hint = make_token_hint(webhook_url)
        meta = {"channel_label": channel_label}
        if existing is None:
            existing = IntegrationConnection(
                provider=provider,
                auth_mode=IntegrationAuthMode.WEBHOOK,
                status=IntegrationStatus.CONNECTED,
                external_account_id=channel_label,
                external_account_name=channel_label,
                access_token_encrypted=enc,
                token_hint=hint,
                metadata_json=meta,
                user_id=connected_by_id,
                last_verified_at=utcnow(),
            )
            session.add(existing)
        else:
            existing.status = IntegrationStatus.CONNECTED
            existing.external_account_id = channel_label
            existing.external_account_name = channel_label
            existing.access_token_encrypted = enc
            existing.token_hint = hint
            existing.metadata_json = meta
            existing.last_verified_at = utcnow()
        await session.flush()
        return existing

    @staticmethod
    async def dispatch_event(
        session: AsyncSession,
        *,
        event_type: str,
        title: str,
        message: str,
        severity: str = "info",
        dedupe_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> list[NotificationDelivery]:
        if dedupe_key:
            dup = (
                await session.execute(
                    select(NotificationDelivery)
                    .where(
                        NotificationDelivery.dedupe_key == dedupe_key,
                        NotificationDelivery.status == DeliveryStatus.SENT,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if dup is not None:
                return []

        conns = list(
            (
                await session.execute(
                    select(IntegrationConnection).where(
                        IntegrationConnection.provider.in_(
                            [IntegrationProvider.SLACK, IntegrationProvider.DISCORD]
                        ),
                        IntegrationConnection.status == IntegrationStatus.CONNECTED,
                    )
                )
            )
            .scalars()
            .all()
        )
        deliveries: list[NotificationDelivery] = []
        clean_title = scrub_secrets(title)
        clean_msg = scrub_secrets(message)

        for conn in conns:
            delivery = NotificationDelivery(
                provider=conn.provider.value,
                channel_target=conn.external_account_name or "default",
                event_type=event_type,
                severity=severity,
                status=DeliveryStatus.PENDING,
                dedupe_key=dedupe_key,
            )
            session.add(delivery)
            try:
                webhook_url = decrypt_token(conn.access_token_encrypted or "")
                payload = (
                    {"text": f"*[{severity.upper()}] {clean_title}*\n{clean_msg}"}
                    if conn.provider == IntegrationProvider.SLACK
                    else {"content": f"**[{severity.upper()}] {clean_title}**\n{clean_msg}"}
                )
                async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
                    resp = await client.post(webhook_url, json=payload)
                delivery.response_status = resp.status_code
                if 200 <= resp.status_code < 300:
                    delivery.status = DeliveryStatus.SENT
                    delivery.sent_at = utcnow()
                elif resp.status_code == 429:
                    delivery.status = DeliveryStatus.RATE_LIMITED
                    delivery.error_message = "Provider rate limit (429)."
                else:
                    delivery.status = DeliveryStatus.FAILED
                    delivery.error_message = f"HTTP {resp.status_code}"
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                delivery.status = DeliveryStatus.FAILED
                delivery.error_message = str(exc)[:300]
            deliveries.append(delivery)

        await session.flush()
        return deliveries
