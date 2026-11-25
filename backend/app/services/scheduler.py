"""Database-backed Persistent Scheduler worker & execution engine for ReleaseGuard AI."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import FastAPI
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.specialists.pr_agent import analyze_pr_with_static_and_gemini
from app.agents.tools.context import ToolExecutionContext
from app.agents.tools.experiment_tools import (
    handle_compute_bayesian_experiment_analysis,
    handle_evaluate_release_recommendation,
)
from app.agents.tools.flag_tools import handle_scan_flag_health
from app.api.deps import (
    build_flagsmith_client,
    build_gemini_client_from_app,
    build_github_client,
    build_supervisor,
    build_tool_context,
    resolve_session_factory,
)
from app.api.v1.repositories import sync_pull_requests_for_repo
from app.core.config import get_settings
from app.core.database import ensure_utc, get_session_factory, utcnow
from app.core.security import scrub_secrets
from app.models import (
    ActorType,
    AgentSession,
    AgentSessionStatus,
    AgentTask,
    AgentTaskStatus,
    AnalysisStatus,
    Approval,
    ApprovalStatus,
    AuditLog,
    AuditStatus,
    Experiment,
    ExperimentStatus,
    FeatureFlag,
    JobExecution,
    JobExecutionStatus,
    JobTriggerType,
    NotificationCategory,
    PolicyCategory,
    PullRequest,
    PullRequestState,
    ReleaseRecommendation,
    Repository,
    RiskLevel,
    RolloutHistory,
    ScheduledJob,
    ScheduleFrequency,
    ScheduleJobType,
    SpecialistRole,
)
from app.schemas.tools import (
    ComputeBayesianExperimentAnalysisInput,
    EvaluateReleaseRecommendationInput,
    ScanFlagHealthInput,
)
from app.services.cron_utils import (
    compute_next_run_at,
    validate_custom_schedule_prompt,
)
from app.services.notification_service import (
    broadcast_workspace_event,
    create_notification,
)
from app.services.policy_engine import PolicyEngine

logger = logging.getLogger("releaseguard.scheduler")


class JobAlreadyRunningError(RuntimeError):
    """Raised when an atomic lease check detects an active worker lock for the job."""


class SchedulerService:
    """Async PostgreSQL/SQLite-backed scheduler with atomic leases, retries, and stale recovery."""

    def __init__(
        self,
        *,
        app: FastAPI | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        poll_interval_seconds: float = 10.0,
        instance_id: str | None = None,
    ) -> None:
        self.app = app
        self._explicit_factory = session_factory
        self.poll_interval_seconds = max(1.0, float(poll_interval_seconds))
        self.instance_id = instance_id or f"scheduler-{uuid.uuid4().hex[:8]}"
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self.last_tick_at: datetime | None = None
        self.last_error: str | None = None
        self.ticks_completed: int = 0
        self.started_at: datetime | None = None

    def get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._explicit_factory is not None:
            return self._explicit_factory
        if self.app is not None:
            return resolve_session_factory(self.app)
        return get_session_factory()

    async def start(self) -> None:
        if self._running:
            return
        await self.recover_stale_runs()
        self._running = True
        self.started_at = utcnow()
        self._task = asyncio.create_task(self._poll_loop(), name=f"rg-{self.instance_id}")
        logger.info("SchedulerService %s started (poll=%ss)", self.instance_id, self.poll_interval_seconds)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("SchedulerService %s stopped", self.instance_id)

    def get_status(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "running": self._running,
            "poll_interval_seconds": self.poll_interval_seconds,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
            "ticks_completed": self.ticks_completed,
            "last_error": self.last_error,
        }

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self.tick_once()
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = scrub_secrets(str(exc))[:500]
                logger.exception("Scheduler tick failed: %s", self.last_error)
            await asyncio.sleep(self.poll_interval_seconds)

    async def recover_stale_runs(self) -> dict[str, int]:
        """Clear stale locks and mark interrupted RUNNING/RETRYING executions FAILED on startup."""
        factory = self.get_session_factory()
        now = utcnow()
        stale_execs = 0
        cleared_locks = 0
        missed_jobs = 0

        async with factory() as session:
            running_execs = (
                (
                    await session.execute(
                        select(JobExecution).where(
                            JobExecution.execution_status.in_(
                                [JobExecutionStatus.RUNNING, JobExecutionStatus.RETRYING]
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
            for ex in running_execs:
                ex.execution_status = JobExecutionStatus.FAILED
                ex.completed_at = now
                ex.error_details = (
                    ex.error_details
                    or "Interrupted by worker/container restart; stale lock recovered."
                )
                stale_execs += 1
                if ex.scheduled_job_id is not None:
                    parent = await session.get(ScheduledJob, ex.scheduled_job_id)
                    if parent is not None and parent.last_status == "running":
                        parent.last_status = "failed"
                        parent.consecutive_failures += 1

            lock_res = await session.execute(
                update(ScheduledJob)
                .where(or_(ScheduledJob.locked_by.is_not(None), ScheduledJob.locked_until.is_not(None)))
                .values(locked_by=None, locked_until=None)
            )
            cleared_locks = int(lock_res.rowcount or 0)

            due_jobs = (
                (
                    await session.execute(
                        select(ScheduledJob).where(
                            ScheduledJob.enabled.is_(True),
                            ScheduledJob.next_run_at.is_not(None),
                            ScheduledJob.next_run_at < now - timedelta(seconds=60),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for job in due_jobs:
                job.missed_runs_count += 1
                missed_jobs += 1

            await session.commit()

        return {
            "recovered_executions": stale_execs,
            "cleared_locks": cleared_locks,
            "missed_jobs": missed_jobs,
        }

    async def tick_once(self, *, now: datetime | None = None) -> list[uuid.UUID]:
        """Claim due enabled schedules whose next_run_at <= now and execute them."""
        check_now = ensure_utc(now) or utcnow()
        self.last_tick_at = check_now
        self.ticks_completed += 1

        factory = self.get_session_factory()
        async with factory() as session:
            stmt = (
                select(ScheduledJob.id)
                .where(
                    ScheduledJob.enabled.is_(True),
                    ScheduledJob.next_run_at.is_not(None),
                    ScheduledJob.next_run_at <= check_now,
                    or_(ScheduledJob.locked_until.is_(None), ScheduledJob.locked_until <= check_now),
                )
                .order_by(ScheduledJob.next_run_at.asc())
                .limit(10)
            )
            due_ids = list((await session.execute(stmt)).scalars().all())

        executed_ids: list[uuid.UUID] = []
        for job_id in due_ids:
            try:
                exec_row = await self.run_job_by_id(
                    job_id,
                    trigger_type=JobTriggerType.SCHEDULED,
                    now=check_now,
                )
                executed_ids.append(exec_row.id)
            except JobAlreadyRunningError:
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("Scheduled run failed for job %s: %s", job_id, scrub_secrets(str(exc)))
        return executed_ids

    async def _acquire_lease(
        self,
        session: AsyncSession,
        job_id: uuid.UUID,
        *,
        timeout_seconds: int,
        trigger_type: JobTriggerType,
        now: datetime,
    ) -> ScheduledJob:
        lease_until = now + timedelta(seconds=max(30, timeout_seconds + 15))
        cond = and_(
            ScheduledJob.id == job_id,
            or_(ScheduledJob.locked_until.is_(None), ScheduledJob.locked_until <= now),
        )
        if trigger_type == JobTriggerType.SCHEDULED:
            cond = and_(
                cond,
                ScheduledJob.enabled.is_(True),
                ScheduledJob.next_run_at.is_not(None),
                ScheduledJob.next_run_at <= now,
            )

        res = await session.execute(
            update(ScheduledJob)
            .where(cond)
            .values(locked_by=self.instance_id, locked_until=lease_until, last_status="running")
        )
        if int(res.rowcount or 0) != 1:
            await session.rollback()
            raise JobAlreadyRunningError(f"Job {job_id} is already locked or no longer due.")

        await session.commit()
        job = await session.get(ScheduledJob, job_id)
        if job is None:
            raise ValueError(f"ScheduledJob {job_id} not found after lease acquisition.")
        return job

    async def _release_lease(self, session: AsyncSession, job_id: uuid.UUID) -> None:
        await session.execute(
            update(ScheduledJob)
            .where(ScheduledJob.id == job_id)
            .values(locked_by=None, locked_until=None)
        )
        await session.commit()

    async def run_job_by_id(
        self,
        job_id: uuid.UUID,
        *,
        trigger_type: JobTriggerType = JobTriggerType.MANUAL,
        triggered_by_user_id: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> JobExecution:
        """Execute one scheduled job safely under DB lease, timeout, retry, and audit trail."""
        run_started = ensure_utc(now) or utcnow()
        factory = self.get_session_factory()

        async with factory() as session:
            existing = await session.get(ScheduledJob, job_id)
            if existing is None:
                raise ValueError(f"ScheduledJob {job_id} not found.")
            retry_cfg = dict(existing.retry_policy or {})
            timeout_seconds = max(5, min(1800, int(retry_cfg.get("timeout_seconds", 120))))
            max_retries = max(0, min(5, int(retry_cfg.get("max_retries", 2))))
            retry_delay = max(1, min(3600, int(retry_cfg.get("retry_delay_seconds", 30))))

            job = await self._acquire_lease(
                session,
                job_id,
                timeout_seconds=timeout_seconds,
                trigger_type=trigger_type,
                now=run_started,
            )

            exec_row = JobExecution(
                scheduled_job_id=job.id,
                schedule_name=job.name,
                job_type=job.job_type,
                execution_status=JobExecutionStatus.RUNNING,
                trigger_type=trigger_type,
                started_at=run_started,
                input_payload=dict(job.configuration or {}),
            )
            session.add(exec_row)
            await session.commit()
            await session.refresh(exec_row)
            execution_id = exec_row.id

        last_err_text: str | None = None
        result_summary: dict[str, Any] | None = None
        agent_task_id: uuid.UUID | None = None
        resource_type: str | None = None
        resource_id: str | None = None
        succeeded = False

        try:
            for attempt in range(max_retries + 1):
                try:
                    async with factory() as exec_session:
                        job_obj = await exec_session.get(ScheduledJob, job_id)
                        if job_obj is None:
                            raise ValueError(f"ScheduledJob {job_id} vanished during execution.")
                        handler_out = await asyncio.wait_for(
                            self._dispatch_job_handler(
                                exec_session,
                                job_obj,
                                execution_id=execution_id,
                            ),
                            timeout=timeout_seconds,
                        )
                        result_summary = dict(handler_out.get("summary") or {})
                        if handler_out.get("agent_task_id"):
                            agent_task_id = uuid.UUID(str(handler_out["agent_task_id"]))
                        resource_type = handler_out.get("resource_type")
                        resource_id = handler_out.get("resource_id")
                        await exec_session.commit()
                    succeeded = True
                    break
                except Exception as exc:  # noqa: BLE001
                    last_err_text = scrub_secrets(str(exc))[:1800] or type(exc).__name__
                    if attempt < max_retries:
                        async with factory() as retry_session:
                            row = await retry_session.get(JobExecution, execution_id)
                            if row is not None:
                                row.execution_status = JobExecutionStatus.RETRYING
                                row.retry_count = attempt + 1
                                row.error_details = last_err_text
                                await retry_session.commit()
                        await asyncio.sleep(retry_delay)
                    else:
                        break
        finally:
            completed_at = utcnow()
            async with factory() as fin_session:
                job_obj = await fin_session.get(ScheduledJob, job_id)
                exec_obj = await fin_session.get(JobExecution, execution_id)

                if exec_obj is not None:
                    exec_obj.completed_at = completed_at
                    exec_obj.execution_status = (
                        JobExecutionStatus.COMPLETED if succeeded else JobExecutionStatus.FAILED
                    )
                    exec_obj.result_summary = result_summary
                    exec_obj.error_details = None if succeeded else last_err_text
                    exec_obj.agent_task_id = agent_task_id
                    exec_obj.resource_type = resource_type
                    exec_obj.resource_id = resource_id

                if job_obj is not None:
                    job_obj.last_run_at = completed_at
                    job_obj.last_status = "completed" if succeeded else "failed"
                    job_obj.locked_by = None
                    job_obj.locked_until = None
                    if succeeded:
                        job_obj.consecutive_failures = 0
                    else:
                        job_obj.consecutive_failures += 1

                    if trigger_type == JobTriggerType.SCHEDULED:
                        if job_obj.frequency == ScheduleFrequency.ONCE:
                            job_obj.enabled = False
                            job_obj.next_run_at = None
                        else:
                            nxt = compute_next_run_at(
                                frequency=job_obj.frequency.value,
                                timezone=job_obj.timezone,
                                cron_expression=job_obj.cron_expression,
                                interval_minutes=job_obj.interval_minutes,
                                start_at=job_obj.start_at,
                                end_at=job_obj.end_at,
                                reference_utc=completed_at,
                                initial=False,
                            )
                            job_obj.next_run_at = nxt
                            if nxt is None and job_obj.end_at is not None:
                                job_obj.enabled = False

                await PolicyEngine.record_audit_log(
                    fin_session,
                    actor_type=ActorType.USER if triggered_by_user_id else ActorType.SYSTEM,
                    actor_id=str(triggered_by_user_id or self.instance_id),
                    policy_category=PolicyCategory.A,
                    event_action=(
                        "schedule.execution_succeeded" if succeeded else "schedule.execution_failed"
                    ),
                    resource_type="scheduled_job",
                    resource_id=str(job_id),
                    status=AuditStatus.SUCCESS if succeeded else AuditStatus.FAILED,
                    details={
                        "execution_id": str(execution_id),
                        "job_type": job_obj.job_type.value if job_obj else "unknown",
                        "trigger_type": trigger_type.value,
                        "retry_count": exec_obj.retry_count if exec_obj else 0,
                        "error": last_err_text if not succeeded else None,
                    },
                )
                if job_obj is not None:
                    notif_pref = dict(job_obj.notification_preference or {})
                    if not succeeded and bool(notif_pref.get("on_failure", True)):
                        await create_notification(
                            fin_session,
                            category=NotificationCategory.SCHEDULE_FAILED,
                            severity="high",
                            title=f"Scheduled Job Failed: {job_obj.name}",
                            message=last_err_text or f"Job `{job_obj.job_type.value}` failed.",
                            user_id=job_obj.owner_id,
                            resource_type="scheduled_job",
                            resource_id=str(job_obj.id),
                            action_href="/schedules",
                            metadata={"execution_id": str(execution_id), "job_type": job_obj.job_type.value},
                        )
                    elif succeeded and bool(notif_pref.get("on_success", False)):
                        await create_notification(
                            fin_session,
                            category=NotificationCategory.SCHEDULE_COMPLETED,
                            severity="info",
                            title=f"Scheduled Job Completed: {job_obj.name}",
                            message=f"`{job_obj.job_type.value}` finished ({trigger_type.value}).",
                            user_id=job_obj.owner_id,
                            resource_type="scheduled_job",
                            resource_id=str(job_obj.id),
                            action_href="/schedules",
                            metadata={"execution_id": str(execution_id), "summary": result_summary or {}},
                        )
                    await broadcast_workspace_event(
                        "schedule.executed",
                        {
                            "scheduled_job_id": str(job_obj.id),
                            "execution_id": str(execution_id),
                            "status": "completed" if succeeded else "failed",
                            "job_type": job_obj.job_type.value,
                        },
                    )
                await fin_session.commit()
                if exec_obj is not None:
                    await fin_session.refresh(exec_obj)
                    final_exec = exec_obj
                else:
                    raise RuntimeError(f"JobExecution {execution_id} missing at completion.")

        return final_exec

    async def _get_github_client(self, session: AsyncSession) -> Any:
        if self.app is not None:
            return await build_github_client(self.app, session)
        return await ToolExecutionContext(session=session).get_github_client()

    async def _get_flagsmith_client(self, session: AsyncSession) -> Any:
        if self.app is not None:
            return await build_flagsmith_client(self.app, session)
        return await ToolExecutionContext(session=session).get_flagsmith_client()

    async def _build_tool_ctx(self, session: AsyncSession, job: ScheduledJob) -> ToolExecutionContext:
        if self.app is not None:
            return await build_tool_context(
                self.app,
                session,
                user_id=job.owner_id,
                context_scope={"scheduled_job_id": str(job.id), **(job.configuration or {})},
            )
        return ToolExecutionContext(
            session=session,
            user_id=job.owner_id,
            context_scope={"scheduled_job_id": str(job.id), **(job.configuration or {})},
        )

    async def _dispatch_job_handler(
        self,
        session: AsyncSession,
        job: ScheduledJob,
        *,
        execution_id: uuid.UUID,
    ) -> dict[str, Any]:
        cfg = dict(job.configuration or {})

        # Optional test-only hook (strictly restricted to APP_ENV == 'test')
        if self.app is not None and get_settings().APP_ENV == "test":
            test_hook = getattr(self.app.state, "scheduler_job_hook", None)
            if callable(test_hook):
                return await test_hook(session, job, execution_id)

        if job.job_type == ScheduleJobType.REPO_SYNC:
            return await self._handle_repo_sync(session, cfg)
        if job.job_type == ScheduleJobType.PR_ANALYSIS:
            return await self._handle_pr_analysis(session, cfg)
        if job.job_type == ScheduleJobType.FLAG_HEALTH_SCAN:
            return await self._handle_flag_health_scan(session, job, cfg)
        if job.job_type == ScheduleJobType.EXPERIMENT_MONITOR:
            return await self._handle_experiment_monitor(session, job, cfg)
        if job.job_type == ScheduleJobType.RELEASE_MONITOR:
            return await self._handle_release_monitor(session, cfg)
        if job.job_type == ScheduleJobType.AI_MAINTENANCE_REPORT:
            return await self._handle_ai_maintenance_report(session, cfg)
        if job.job_type == ScheduleJobType.CUSTOM_AGENT_TASK:
            return await self._handle_custom_agent_task(session, job, cfg, execution_id=execution_id)
        raise ValueError(f"Unsupported scheduled job_type: {job.job_type}")

    async def _handle_repo_sync(self, session: AsyncSession, cfg: dict[str, Any]) -> dict[str, Any]:
        gh = await self._get_github_client(session)
        repo_id = cfg.get("repository_id")
        if repo_id:
            repo = await session.get(Repository, uuid.UUID(str(repo_id)))
            repos = [repo] if repo is not None else []
        else:
            repos = list((await session.execute(select(Repository))).scalars().all())

        total_prs = 0
        synced_repos = 0
        pr_state = str(cfg.get("state") or "open")
        for repo in repos:
            synced = await sync_pull_requests_for_repo(session, gh, repo, state=pr_state)
            total_prs += len(synced)
            synced_repos += 1

        return {
            "summary": {
                "repositories_synced": synced_repos,
                "pull_requests_synced": total_prs,
            },
            "resource_type": "repository",
            "resource_id": str(repo_id) if repo_id else None,
        }

    async def _handle_pr_analysis(self, session: AsyncSession, cfg: dict[str, Any]) -> dict[str, Any]:
        repo_id = cfg.get("repository_id")
        pr_id = cfg.get("pull_request_id")
        force = bool(cfg.get("force", False))
        limit = max(1, min(20, int(cfg.get("max_prs", 5))))

        if pr_id:
            pr = await session.get(PullRequest, uuid.UUID(str(pr_id)))
            prs = [pr] if pr is not None else []
        else:
            stmt = select(PullRequest).where(PullRequest.state == PullRequestState.OPEN)
            if repo_id:
                stmt = stmt.where(PullRequest.repository_id == uuid.UUID(str(repo_id)))
            prs = list((await session.execute(stmt.order_by(PullRequest.updated_at.desc()).limit(limit))).scalars().all())

        gh = await self._get_github_client(session) if prs else None
        ws = await PolicyEngine.get_workspace_settings(session)
        gemini = (
            build_gemini_client_from_app(self.app, model_override=ws.gemini_model)
            if self.app is not None
            else build_gemini_client_from_app(FastAPI(), model_override=ws.gemini_model)
        )

        analyzed_count = 0
        skipped_unchanged = 0
        high_risk_found = 0

        for pr in prs:
            if not force and pr.analysis_status == AnalysisStatus.COMPLETED and pr.analyzed_at is not None:
                # Check whether head_sha changed or PR updated since last analyzed audit record
                last_audit = (
                    await session.execute(
                        select(AuditLog)
                        .where(
                            AuditLog.event_action == "pr.analyzed",
                            AuditLog.resource_id == str(pr.id),
                        )
                        .order_by(AuditLog.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                last_sha = (last_audit.details or {}).get("head_sha") if last_audit else None
                pr_updated = ensure_utc(pr.updated_at)
                pr_analyzed = ensure_utc(pr.analyzed_at)
                if last_sha == pr.head_sha or (
                    last_sha is None and pr_updated is not None and pr_analyzed is not None and pr_updated <= pr_analyzed
                ):
                    skipped_unchanged += 1
                    continue

            repo = await session.get(Repository, pr.repository_id)
            if repo is None or gh is None:
                continue

            files_data = await gh.get_pull_request_files(repo.owner, repo.name, pr.github_pr_number)
            pr.analysis_status = AnalysisStatus.ANALYZING
            await session.flush()

            analysis = await analyze_pr_with_static_and_gemini(
                pr_title=pr.title,
                pr_body=pr.body,
                files_data=files_data,
                repo_full_name=repo.full_name,
                pr_number=pr.github_pr_number,
                gemini_client=gemini,
            )
            pr.risk_score = int(analysis["risk_score"])
            pr.risk_level = RiskLevel(str(analysis["risk_level"]).lower())
            pr.analysis_summary = str(analysis["analysis_summary"])
            pr.confirmed_risks = list(analysis["confirmed_risks"])
            pr.potential_risks = list(analysis["potential_risks"])
            pr.flag_opportunities = list(analysis["flag_opportunities"])
            pr.analysis_status = AnalysisStatus.COMPLETED
            pr.analyzed_at = utcnow()
            analyzed_count += 1
            if pr.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                high_risk_found += 1
                await create_notification(
                    session,
                    category=NotificationCategory.PR_HIGH_RISK,
                    severity="critical" if pr.risk_level == RiskLevel.CRITICAL else "high",
                    title=f"{pr.risk_level.value.upper()} Risk PR #{pr.github_pr_number}: {pr.title[:160]}",
                    message=f"Scheduled PR scan scored {pr.risk_score}/100 ({len(pr.confirmed_risks or [])} confirmed risk(s)).",
                    resource_type="pull_request",
                    resource_id=str(pr.id),
                    action_href="/pull-requests",
                    metadata={"risk_score": pr.risk_score, "trigger": "scheduled"},
                    dedupe_minutes=60,
                )

            await PolicyEngine.record_audit_log(
                session,
                actor_type=ActorType.SYSTEM,
                actor_id=self.instance_id,
                policy_category=PolicyCategory.A,
                event_action="pr.analyzed",
                resource_type="pull_request",
                resource_id=str(pr.id),
                status=AuditStatus.SUCCESS,
                details={
                    "risk_score": pr.risk_score,
                    "risk_level": pr.risk_level.value,
                    "head_sha": pr.head_sha,
                    "trigger": "scheduled",
                },
            )

        return {
            "summary": {
                "prs_evaluated": len(prs),
                "prs_analyzed": analyzed_count,
                "skipped_unchanged": skipped_unchanged,
                "high_risk_prs": high_risk_found,
            },
            "resource_type": "pull_request",
            "resource_id": str(pr_id) if pr_id else (str(repo_id) if repo_id else None),
        }

    async def _handle_flag_health_scan(
        self, session: AsyncSession, job: ScheduledJob, cfg: dict[str, Any]
    ) -> dict[str, Any]:
        ctx = await self._build_tool_ctx(session, job)
        pid = int(cfg["project_id"]) if cfg.get("project_id") is not None else None
        repo_id = uuid.UUID(str(cfg["repository_id"])) if cfg.get("repository_id") else None
        out = await handle_scan_flag_health(ctx, ScanFlagHealthInput(project_id=pid, repository_id=repo_id))
        unhealthy = int(out.get("unhealthy_flags", 0))
        if unhealthy > 0:
            await create_notification(
                session,
                category=NotificationCategory.FLAG_HEALTH_ALERT,
                severity="warning",
                title=f"Flag Health Alert: {unhealthy} Flag(s) Need Attention",
                message=f"Scheduled flag health scan found {unhealthy} unhealthy flag(s) across {out.get('flags_scanned', 0)} scanned.",
                resource_type="feature_flag",
                resource_id=str(pid) if pid is not None else "workspace",
                action_href="/feature-flags",
                metadata={"unhealthy_flags": unhealthy, "flags_scanned": out.get("flags_scanned", 0)},
                dedupe_minutes=60,
            )
        return {
            "summary": {
                "flags_scanned": out.get("flags_scanned", 0),
                "unhealthy_flags": unhealthy,
            },
            "resource_type": "feature_flag",
            "resource_id": str(pid) if pid is not None else None,
        }

    async def _handle_experiment_monitor(
        self, session: AsyncSession, job: ScheduledJob, cfg: dict[str, Any]
    ) -> dict[str, Any]:
        ctx = await self._build_tool_ctx(session, job)
        exp_id = cfg.get("experiment_id")
        env_filter = str(cfg.get("environment_name") or "").strip().lower() or None

        if exp_id:
            exp = await session.get(Experiment, uuid.UUID(str(exp_id)))
            experiments = [exp] if exp is not None else []
        else:
            stmt = select(Experiment).where(Experiment.status == ExperimentStatus.RUNNING)
            if env_filter:
                stmt = stmt.where(Experiment.environment_name == env_filter)
            experiments = list((await session.execute(stmt)).scalars().all())

        evaluated = 0
        breaches = 0
        recommendations: dict[str, str] = {}
        for exp in experiments:
            analysis_out = await handle_compute_bayesian_experiment_analysis(
                ctx, ComputeBayesianExperimentAnalysisInput(experiment_id=exp.id)
            )
            rec_out = await handle_evaluate_release_recommendation(
                ctx, EvaluateReleaseRecommendationInput(experiment_id=exp.id)
            )
            evaluated += 1
            rec_val = str(rec_out.get("recommendation") or "")
            recommendations[str(exp.id)] = rec_val
            guardrails = (analysis_out.get("analysis") or {}).get("guardrails") or {}
            pf_breached = bool((guardrails.get("payment_failed") or {}).get("breached"))
            ab_breached = bool((guardrails.get("checkout_abandoned") or {}).get("breached"))
            if pf_breached or ab_breached or rec_val == ReleaseRecommendation.RECOMMEND_ROLLBACK.value:
                breaches += 1
                await create_notification(
                    session,
                    category=NotificationCategory.GUARDRAIL_BREACH,
                    severity="critical",
                    title=f"Scheduled Monitor Guardrail Breach: {exp.name}",
                    message=f"Experiment `{exp.name}` in `{exp.environment_name}` recommendation: {rec_val or 'Breach detected'}.",
                    resource_type="experiment",
                    resource_id=str(exp.id),
                    environment_name=exp.environment_name,
                    action_href=f"/experiments?id={exp.id}",
                    metadata={"recommendation": rec_val, "trigger": "scheduled"},
                    dedupe_minutes=30,
                )

        return {
            "summary": {
                "experiments_evaluated": evaluated,
                "guardrail_breaches": breaches,
                "recommendations": recommendations,
            },
            "resource_type": "experiment",
            "resource_id": str(exp_id) if exp_id else None,
        }

    async def _handle_release_monitor(self, session: AsyncSession, cfg: dict[str, Any]) -> dict[str, Any]:
        now = utcnow()
        env_filter = str(cfg.get("environment_name") or "").strip().lower() or None

        # Mark genuinely expired pending approvals (read/hygiene only; never auto-approves!)
        expired_count = 0
        pending_stmt = select(Approval).where(Approval.status == ApprovalStatus.PENDING)
        for app in (await session.execute(pending_stmt)).scalars().all():
            exp_at = ensure_utc(app.expires_at)
            if exp_at is not None and exp_at < now:
                app.status = ApprovalStatus.EXPIRED
                expired_count += 1

        rollout_stmt = select(RolloutHistory).order_by(RolloutHistory.created_at.desc()).limit(25)
        if env_filter:
            rollout_stmt = rollout_stmt.where(RolloutHistory.environment_name == env_filter)
        recent_rollouts = list((await session.execute(rollout_stmt)).scalars().all())
        unverified_count = sum(1 for r in recent_rollouts if not r.verified)

        breached_exps = sum(
            1
            for e in (await session.execute(select(Experiment).where(Experiment.status == ExperimentStatus.RUNNING))).scalars().all()
            if e.latest_recommendation == ReleaseRecommendation.RECOMMEND_ROLLBACK
        )

        flag_count = len((await session.execute(select(FeatureFlag.id))).all())

        from app.models import Release
        from app.services.release_readiness import ReleaseReadinessService
        from app.services.release_state_machine import TERMINAL_RELEASE_STATES

        rel_stmt = select(Release).where(Release.status.notin_(list(TERMINAL_RELEASE_STATES)))
        if env_filter:
            rel_stmt = rel_stmt.where(Release.target_environment == env_filter)
        active_releases = list((await session.execute(rel_stmt.limit(50))).scalars().all())
        for rel in active_releases:
            await ReleaseReadinessService.evaluate_release(session, rel, emit_event=False)

        from app.models import (
            ProgressiveRollout,
            ProgressiveRolloutStage,
            ProgressiveRolloutStatus,
        )
        from app.services.progressive_delivery import (
            ProgressiveDeliveryError,
            ProgressiveDeliveryService,
        )

        pr_stmt = select(ProgressiveRollout).where(
            ProgressiveRollout.status == ProgressiveRolloutStatus.OBSERVING
        )
        if env_filter:
            pr_stmt = pr_stmt.where(ProgressiveRollout.environment == env_filter)
        observing_rollouts = list((await session.execute(pr_stmt.limit(20))).scalars().all())
        evaluated_rollout_stages = 0
        for rollout in observing_rollouts:
            if rollout.current_stage_id:
                try:
                    stage = await session.get(ProgressiveRolloutStage, rollout.current_stage_id)
                    if stage is not None:
                        await ProgressiveDeliveryService.evaluate_stage(session, rollout, stage)
                        evaluated_rollout_stages += 1
                except (ProgressiveDeliveryError, ValueError, RuntimeError) as exc:
                    logger.warning("Scheduler evaluation skipped for rollout %s: %s", rollout.id, exc)

        return {
            "summary": {
                "flags_tracked": flag_count,
                "active_releases_evaluated": len(active_releases),
                "observing_rollouts_evaluated": evaluated_rollout_stages,
                "recent_rollouts_checked": len(recent_rollouts),
                "unverified_rollouts": unverified_count,
                "expired_approvals_closed": expired_count,
                "rollback_recommended_experiments": breached_exps,
            },
            "resource_type": "release",
            "resource_id": env_filter,
        }

    async def _handle_ai_maintenance_report(
        self, session: AsyncSession, cfg: dict[str, Any]
    ) -> dict[str, Any]:
        open_prs = list(
            (await session.execute(select(PullRequest).where(PullRequest.state == PullRequestState.OPEN)))
            .scalars()
            .all()
        )
        high_risk_prs = [p for p in open_prs if p.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)]
        flags = list((await session.execute(select(FeatureFlag))).scalars().all())
        unhealthy_flags = [f for f in flags if f.health_score < 100]
        pending_approvals = list(
            (await session.execute(select(Approval).where(Approval.status == ApprovalStatus.PENDING)))
            .scalars()
            .all()
        )
        running_exps = list(
            (await session.execute(select(Experiment).where(Experiment.status == ExperimentStatus.RUNNING)))
            .scalars()
            .all()
        )
        rollback_recs = [
            e for e in running_exps if e.latest_recommendation == ReleaseRecommendation.RECOMMEND_ROLLBACK
        ]

        from app.services.intelligence import ReleaseLearningEngine

        learning_run = await ReleaseLearningEngine.run_analysis(
            session, analysis_type="scheduled_learning"
        )

        summary_stats = {
            "open_prs": len(open_prs),
            "high_risk_prs": len(high_risk_prs),
            "total_flags": len(flags),
            "unhealthy_flags": len(unhealthy_flags),
            "pending_approvals": len(pending_approvals),
            "running_experiments": len(running_exps),
            "rollback_recommendations": len(rollback_recs),
            "learning_run_status": learning_run.status.value,
        }

        narrative = (
            f"Maintenance digest: {summary_stats['open_prs']} open PRs ({summary_stats['high_risk_prs']} high/critical risk), "
            f"{summary_stats['unhealthy_flags']}/{summary_stats['total_flags']} flags needing health attention, "
            f"{summary_stats['pending_approvals']} pending human approvals, "
            f"{summary_stats['running_experiments']} running experiments ({summary_stats['rollback_recommendations']} rollback advised)."
        )

        if bool(cfg.get("include_ai_prose", False)) and self.app is not None:
            ws = await PolicyEngine.get_workspace_settings(session)
            gemini = build_gemini_client_from_app(self.app, model_override=ws.gemini_model)
            if gemini.is_configured:
                try:
                    resp = await gemini.generate_content(
                        contents=[{"role": "user", "parts": [{"text": str(summary_stats)}]}],
                        system_instruction=(
                            "Summarize release engineering maintenance metrics concisely in 2 sentences "
                            "without inventing numbers."
                        ),
                        temperature=0.1,
                    )
                    text = (getattr(resp, "text", None) or "").strip()
                    if text:
                        narrative = scrub_secrets(text)[:1000]
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Optional Gemini digest prose skipped: %s", exc)

        return {
            "summary": {**summary_stats, "narrative": narrative},
            "resource_type": "workspace",
            "resource_id": "maintenance_report",
        }

    async def _handle_custom_agent_task(
        self,
        session: AsyncSession,
        job: ScheduledJob,
        cfg: dict[str, Any],
        *,
        execution_id: uuid.UUID,
    ) -> dict[str, Any]:
        prompt = str(cfg.get("prompt") or cfg.get("nl_prompt") or job.description or job.name).strip()
        ok, err_msg = validate_custom_schedule_prompt(prompt)
        if not ok:
            raise ValueError(err_msg or "Unsafe custom agent schedule prompt rejected.")

        if self.app is None:
            raise RuntimeError("Custom agent execution requires FastAPI app supervisor context.")

        ws = await PolicyEngine.get_workspace_settings(session)
        agent_sess = AgentSession(
            user_id=job.owner_id,
            title=f"[Scheduled] {job.name}",
            thread_id=f"sched-{job.id.hex[:12]}-{execution_id.hex[:8]}",
            active_specialist=SpecialistRole.SUPERVISOR,
            status=AgentSessionStatus.ACTIVE,
            context_scope={
                "scheduled_job_id": str(job.id),
                "execution_id": str(execution_id),
                **{k: v for k, v in cfg.items() if k in ("repository_id", "environment_name", "flag_key", "experiment_id")},
            },
            message_history=[{"role": "user", "content": prompt, "timestamp": utcnow().isoformat()}],
        )
        session.add(agent_sess)
        await session.flush()

        task = AgentTask(
            session_id=agent_sess.id,
            specialist=SpecialistRole.SUPERVISOR,
            user_prompt=prompt,
            status=AgentTaskStatus.QUEUED,
            gemini_model=ws.gemini_model,
        )
        session.add(task)
        await session.commit()
        await session.refresh(agent_sess)
        await session.refresh(task)

        agent_sess_id = agent_sess.id
        agent_thread_id = agent_sess.thread_id
        agent_scope = dict(agent_sess.context_scope or {})
        task_id = task.id

        supervisor = build_supervisor(self.app, model_override=ws.gemini_model)
        result = await supervisor.run_task(
            task_id=task_id,
            session_id=agent_sess_id,
            thread_id=agent_thread_id,
            user_prompt=prompt,
            specialist_hint=cfg.get("specialist_hint"),
            context_scope=agent_scope,
            user_id=job.owner_id,
        )

        # Reload AgentTask committed by supervisor using local scalar UUIDs (avoid expired ORM IO)
        session.expire_all()
        task_row = await session.get(AgentTask, task_id)
        if task_row is not None and task_row.status == AgentTaskStatus.FAILED:
            raise RuntimeError(task_row.error_message or str(result.get("error") or "Agent task failed."))

        is_awaiting = bool(result.get("__interrupt__")) or (
            task_row is not None and task_row.status == AgentTaskStatus.AWAITING_APPROVAL
        )
        queued_app = result.get("queued_approval") or {}
        approval_id = result.get("approval_id") or queued_app.get("approval_id")
        if is_awaiting and not approval_id:
            app_row = (
                await session.execute(
                    select(Approval)
                    .where(Approval.agent_task_id == task_id)
                    .order_by(Approval.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if app_row is not None:
                approval_id = str(app_row.id)

        agent_status = (
            task_row.status.value
            if task_row is not None
            else ("awaiting_approval" if is_awaiting else str(result.get("status") or "completed"))
        )
        final_resp = (
            task_row.final_response
            if task_row is not None and task_row.final_response
            else result.get("final_response")
        )

        # Explicitly verify scheduled tasks NEVER bypass PolicyEngine approvals
        return {
            "summary": {
                "agent_status": agent_status,
                "final_response": str(final_resp or "")[:600],
                "awaiting_approval": is_awaiting,
                "approval_id": str(approval_id) if approval_id else None,
            },
            "agent_task_id": str(task_id),
            "resource_type": "agent_task",
            "resource_id": str(task_id),
        }


def get_scheduler_service(app: FastAPI) -> SchedulerService:
    """Return app-scoped SchedulerService instance, creating one lazily if needed."""
    svc = getattr(app.state, "scheduler_service", None)
    if svc is None:
        svc = SchedulerService(app=app)
        app.state.scheduler_service = svc
    return svc
