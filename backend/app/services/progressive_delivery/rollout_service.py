"""ProgressiveDeliveryService: state machine, Flagsmith integration, decision engine, and rollback."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import ensure_utc, utcnow
from app.core.security import compute_action_hash, redact_dict, scrub_secrets
from app.models import (
    ActorType,
    Approval,
    ApprovalCategory,
    ApprovalStatus,
    AuditLog,
    AuditStatus,
    FeatureFlag,
    GuardrailEvalStatus,
    GuardrailOperator,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    NotificationCategory,
    PolicyCategory,
    ProgressiveRollout,
    ProgressiveRolloutEvent,
    ProgressiveRolloutStage,
    ProgressiveRolloutStatus,
    ProgressiveStageStatus,
    Release,
    ReleaseStageKey,
    ReleaseStatus,
    RiskClassification,
    RollbackOperation,
    RollbackOperationStatus,
    RolloutAction,
    RolloutDecision,
    RolloutDecisionType,
    RolloutGuardrail,
    RolloutHistory,
)
from app.services.notification_service import (
    broadcast_workspace_event,
    create_notification,
)
from app.services.policy_engine import is_production_environment
from app.services.progressive_delivery.metrics_guardrails import (
    GuardrailEvaluationService,
    MetricCollectionService,
)
from app.services.release_state_machine import record_release_event

logger = logging.getLogger("releaseguard.progressive_delivery")

DEFAULT_STAGE_PERCENTAGES = [1, 5, 10, 25, 50, 100]

TERMINAL_ROLLOUT_STATES: frozenset[ProgressiveRolloutStatus] = frozenset(
    {
        ProgressiveRolloutStatus.COMPLETED,
        ProgressiveRolloutStatus.ROLLED_BACK,
        ProgressiveRolloutStatus.CANCELLED,
    }
)


class ProgressiveDeliveryError(Exception):
    def __init__(self, message: str, *, status_code: int = 422, code: str = "rollout_error") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class ProgressiveDeliveryService:
    """Orchestrates progressive feature-flag rollouts with metric/guardrail gates and approvals."""

    @staticmethod
    async def _next_rollout_key(session: AsyncSession) -> str:
        year = utcnow().year
        count = int(
            (await session.execute(select(func.count(ProgressiveRollout.id)))).scalar_one() or 0
        )
        seq = count + 1
        while True:
            cand = f"PRL-{year}-{seq:04d}"
            exists = (
                await session.execute(
                    select(ProgressiveRollout.id)
                    .where(ProgressiveRollout.rollout_key == cand)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if exists is None:
                return cand
            seq += 1

    @staticmethod
    async def _record_event(
        session: AsyncSession,
        rollout: ProgressiveRollout,
        *,
        event_type: str,
        message: str,
        stage_id: uuid.UUID | None = None,
        actor_id: uuid.UUID | None = None,
        actor_type: ActorType = ActorType.SYSTEM,
        data: dict[str, Any] | None = None,
    ) -> ProgressiveRolloutEvent:
        ev = ProgressiveRolloutEvent(
            rollout_id=rollout.id,
            stage_id=stage_id,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            message=scrub_secrets(message),
            structured_data=redact_dict(dict(data or {})),
        )
        session.add(ev)
        await session.flush()
        await broadcast_workspace_event(
            f"rollout.{event_type}",
            {
                "rollout_id": str(rollout.id),
                "rollout_key": rollout.rollout_key,
                "status": rollout.status.value,
                "current_percentage": rollout.current_percentage,
            },
        )
        return ev

    @staticmethod
    def _validate_stage_sequence(stages: list[int]) -> list[int]:
        if not stages:
            return list(DEFAULT_STAGE_PERCENTAGES)
        cleaned = [int(p) for p in stages]
        if any(p <= 0 or p > 100 for p in cleaned):
            raise ProgressiveDeliveryError("Stage percentages must be between 1 and 100.")
        if cleaned != sorted(set(cleaned)):
            raise ProgressiveDeliveryError("Stage percentages must be strictly increasing.")
        if cleaned[-1] != 100:
            raise ProgressiveDeliveryError("Final rollout stage must target 100%.")
        return cleaned

    @staticmethod
    async def create_rollout(
        session: AsyncSession,
        *,
        name: str,
        feature_flag_id: uuid.UUID,
        environment: str = "development",
        description: str | None = None,
        release_id: uuid.UUID | None = None,
        experiment_id: uuid.UUID | None = None,
        stage_percentages: list[int] | None = None,
        primary_metric_key: str = "checkout_completed",
        observation_config: dict[str, Any] | None = None,
        guardrails_spec: list[dict[str, Any]] | None = None,
        actor_id: uuid.UUID | None = None,
    ) -> ProgressiveRollout:
        flag = await session.get(FeatureFlag, feature_flag_id)
        if flag is None:
            raise ProgressiveDeliveryError("Feature flag not found.", status_code=404)
        if flag.is_archived:
            raise ProgressiveDeliveryError("Cannot create rollout for an archived feature flag.")

        env_norm = environment.strip().lower() or "development"
        conflict = (
            await session.execute(
                select(ProgressiveRollout)
                .where(
                    ProgressiveRollout.feature_flag_id == feature_flag_id,
                    ProgressiveRollout.environment == env_norm,
                    ProgressiveRollout.status.notin_(list(TERMINAL_ROLLOUT_STATES)),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if conflict is not None:
            raise ProgressiveDeliveryError(
                f"Active rollout {conflict.rollout_key} already controls '{flag.key}' in '{env_norm}'.",
                status_code=409,
            )

        release = await session.get(Release, release_id) if release_id else None
        if release_id and release is None:
            raise ProgressiveDeliveryError("Release not found.", status_code=404)
        if release is not None and release.status in {
            ReleaseStatus.CANCELLED,
            ReleaseStatus.ROLLED_BACK,
        }:
            raise ProgressiveDeliveryError("Cannot create rollout for a cancelled or rolled-back release.")

        stages_pct = ProgressiveDeliveryService._validate_stage_sequence(
            stage_percentages or list(DEFAULT_STAGE_PERCENTAGES)
        )
        obs_cfg = {
            "minimum_observation_minutes": 15,
            "maximum_observation_minutes": 60,
            "evaluation_interval_seconds": 60,
            "minimum_sample_size": 100,
            "maximum_metric_age_seconds": 180,
            "required_healthy_evaluations": 2,
            **(observation_config or {}),
        }

        rollout_key = await ProgressiveDeliveryService._next_rollout_key(session)
        rollout = ProgressiveRollout(
            rollout_key=rollout_key,
            name=name.strip()[:255],
            description=description,
            release_id=release.id if release else None,
            repository_id=release.repository_id if release else flag.repository_id,
            pull_request_id=release.pull_request_id if release else None,
            feature_flag_id=flag.id,
            feature_flag_key=flag.key,
            experiment_id=experiment_id or (release.experiment_id if release else None),
            environment=env_norm,
            status=ProgressiveRolloutStatus.DRAFT,
            primary_metric_key=primary_metric_key,
            rollout_configuration={"stages": stages_pct},
            observation_configuration=obs_cfg,
            rollback_configuration={"target_percentage": 0, "disable_flag": True},
            created_by_id=actor_id,
        )
        session.add(rollout)
        await session.flush()

        min_obs_sec = int(obs_cfg.get("minimum_observation_minutes", 15)) * 60
        max_obs_sec = int(obs_cfg.get("maximum_observation_minutes", 60)) * 60
        min_samples = int(obs_cfg.get("minimum_sample_size", 100))

        for idx, pct in enumerate(stages_pct, start=1):
            st = ProgressiveRolloutStage(
                rollout_id=rollout.id,
                stage_number=idx,
                target_percentage=pct,
                status=ProgressiveStageStatus.PENDING,
                minimum_observation_seconds=min_obs_sec,
                maximum_observation_seconds=max_obs_sec,
                minimum_sample_size=min_samples,
            )
            session.add(st)
        await session.flush()

        default_guardrails = guardrails_spec or [
            {
                "metric_key": "payment_failed",
                "operator": GuardrailOperator.GREATER_THAN,
                "threshold": 0.03,
                "severity": RiskClassification.CRITICAL,
                "minimum_sample_size": max(20, min_samples // 5),
                "consecutive_breach_count": 1,
            },
            {
                "metric_key": "checkout_abandoned",
                "operator": GuardrailOperator.GREATER_THAN,
                "threshold": 0.25,
                "severity": RiskClassification.HIGH,
                "minimum_sample_size": max(20, min_samples // 5),
                "consecutive_breach_count": 2,
            },
        ]
        for g in default_guardrails:
            session.add(
                RolloutGuardrail(
                    rollout_id=rollout.id,
                    metric_key=str(g["metric_key"]),
                    operator=GuardrailOperator(g.get("operator", GuardrailOperator.GREATER_THAN)),
                    threshold=float(g["threshold"]),
                    severity=RiskClassification(g.get("severity", RiskClassification.HIGH)),
                    evaluation_window_seconds=int(g.get("evaluation_window_seconds", 900)),
                    minimum_sample_size=int(g.get("minimum_sample_size", 20)),
                    consecutive_breach_count=int(g.get("consecutive_breach_count", 1)),
                )
            )
        await session.flush()

        await ProgressiveDeliveryService._record_event(
            session,
            rollout,
            event_type="created",
            actor_id=actor_id,
            actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
            message=f"Created progressive rollout {rollout_key} ({' -> '.join(f'{p}%' for p in stages_pct)}) in {env_norm}.",
        )
        return rollout

    @staticmethod
    async def validate_rollout(
        session: AsyncSession,
        rollout: ProgressiveRollout,
        *,
        actor_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        blockers: list[str] = []
        flag = await session.get(FeatureFlag, rollout.feature_flag_id)
        if flag is None or flag.is_archived:
            blockers.append("Feature flag is missing or archived.")
        if rollout.release_id is not None:
            rel = await session.get(Release, rollout.release_id)
            if rel is None or rel.status in {ReleaseStatus.CANCELLED, ReleaseStatus.ROLLED_BACK}:
                blockers.append("Linked release is missing or terminal.")

        guardrails = list(
            (
                await session.execute(
                    select(RolloutGuardrail).where(RolloutGuardrail.rollout_id == rollout.id)
                )
            )
            .scalars()
            .all()
        )
        if not guardrails:
            blockers.append("At least one guardrail is required before rollout activation.")

        if blockers:
            rollout.status = ProgressiveRolloutStatus.BLOCKED
            rollout.version += 1
            await ProgressiveDeliveryService._record_event(
                session,
                rollout,
                event_type="validation_failed",
                actor_id=actor_id,
                message="; ".join(blockers),
            )
        else:
            rollout.status = ProgressiveRolloutStatus.READY
            rollout.version += 1
            await ProgressiveDeliveryService._record_event(
                session,
                rollout,
                event_type="validated",
                actor_id=actor_id,
                message="Rollout plan validated and ready for Stage 1 activation.",
            )
        await session.flush()
        return {"valid": not blockers, "blockers": blockers}

    @staticmethod
    async def _resolve_flagsmith_env_key(
        session: AsyncSession,
        flagsmith_client: Any,
        environment_name: str,
        flag: FeatureFlag,
        project_id: int,
    ) -> str:
        env_norm = environment_name.strip().lower()
        if isinstance(flag.env_states, dict):
            for k, state in flag.env_states.items():
                if k.strip().lower() == env_norm and isinstance(state, dict):
                    ek = str(state.get("env_key") or "").strip()
                    if ek:
                        return ek

        stmt = (
            select(IntegrationConnection)
            .where(
                IntegrationConnection.provider == IntegrationProvider.FLAGSMITH,
                IntegrationConnection.status == IntegrationStatus.CONNECTED,
            )
            .order_by(IntegrationConnection.updated_at.desc())
            .limit(1)
        )
        conn = (await session.execute(stmt)).scalar_one_or_none()
        if conn is not None and isinstance(conn.metadata_json, dict):
            env_map = conn.metadata_json.get("environment_keys") or {}
            if isinstance(env_map, dict):
                for k, v in env_map.items():
                    if str(k).strip().lower() == env_norm and str(v).strip():
                        return str(v).strip()

        cfg_keys = get_settings().flagsmith_env_keys
        for k, v in cfg_keys.items():
            if k.strip().lower() == env_norm and v.strip():
                return v.strip()

        envs = await flagsmith_client.list_environments(project_id)
        for env in envs:
            if str(env.get("name", "")).strip().lower() == env_norm and env.get("api_key"):
                return str(env["api_key"])

        raise ProgressiveDeliveryError(
            f"Could not resolve Flagsmith environment key for '{environment_name}'.",
            status_code=422,
            code="flagsmith_env_key_missing",
        )

    @staticmethod
    async def _apply_flagsmith_stage(
        session: AsyncSession,
        rollout: ProgressiveRollout,
        target_pct: int,
        *,
        flagsmith_client: Any | None = None,
        actor_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        flag = await session.get(FeatureFlag, rollout.feature_flag_id)
        if flag is None:
            raise ProgressiveDeliveryError("Feature flag not found.", status_code=404)

        verified = False
        readback: dict[str, Any] = {
            "enabled": target_pct > 0,
            "rollout_percentage": target_pct,
        }
        has_live_client = (
            flagsmith_client is not None
            and bool(str(getattr(flagsmith_client, "org_token", "") or "").strip())
            and flag.flagsmith_feature_id is not None
        )
        if has_live_client:
            try:
                project_id = int(flag.flagsmith_project_id)
            except (TypeError, ValueError) as exc:
                raise ProgressiveDeliveryError(
                    f"Invalid Flagsmith project_id '{flag.flagsmith_project_id}'.",
                    status_code=422,
                    code="flagsmith_project_invalid",
                ) from exc

            feature_id = int(flag.flagsmith_feature_id)
            env_key = await ProgressiveDeliveryService._resolve_flagsmith_env_key(
                session, flagsmith_client, rollout.environment, flag, project_id
            )
            if target_pct == 0:
                await flagsmith_client.rollback_flag(
                    project_id=project_id,
                    env_key=env_key,
                    feature_id=feature_id,
                    flag_key=flag.key,
                )
            else:
                await flagsmith_client.set_gradual_rollout_stage(
                    project_id=project_id,
                    env_key=env_key,
                    feature_id=feature_id,
                    flag_key=flag.key,
                    target_percentage=target_pct,
                    strategy="percentage_split_segment",
                )

            verification = await flagsmith_client.verify_live_state(
                env_key=env_key,
                feature_id=feature_id,
                flag_key=flag.key,
                project_id=project_id,
                expected_enabled=(target_pct == 100),
                expected_percentage=target_pct,
            )
            verified = bool(verification.get("matches", False))
            actual_state = dict(verification.get("actual_state") or {})
            readback = {
                "enabled": bool(actual_state.get("enabled", target_pct > 0)),
                "rollout_percentage": (
                    int(actual_state["rollout_percentage"])
                    if actual_state.get("rollout_percentage") is not None
                    else target_pct
                ),
                "percentage_split": target_pct,
                "env_key": env_key,
                "provider": "flagsmith_live",
                "verification": verification,
            }
            if not verified:
                raise ProgressiveDeliveryError(
                    f"Flagsmith live state verification failed: {verification.get('mismatches')}",
                    status_code=502,
                    code="flagsmith_verification_failed",
                )
        else:
            # Local DB state tracking when external Flagsmith transport isn't attached
            readback["percentage_split"] = target_pct
            readback["provider"] = "local_state_only"
            verified = True

        env_states = dict(flag.env_states or {})
        prev_state = dict(env_states.get(rollout.environment) or {})
        env_states[rollout.environment] = {**prev_state, **readback}
        flag.env_states = env_states

        session.add(
            RolloutHistory(
                feature_flag_id=flag.id,
                environment_name=rollout.environment,
                action=RolloutAction.ROLLBACK if target_pct == 0 else RolloutAction.ROLLOUT_STEP,
                previous_state=prev_state,
                new_state=readback,
                verified=verified,
                verification_details=readback,
                reason=f"Progressive rollout {rollout.rollout_key} -> {target_pct}%",
            )
        )
        session.add(
            AuditLog(
                actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
                actor_id=str(actor_id) if actor_id else None,
                policy_category=(
                    PolicyCategory.C
                    if is_production_environment(rollout.environment)
                    else PolicyCategory.B
                ),
                event_action="progressive_rollout.flag_updated",
                resource_type="feature_flag",
                resource_id=str(flag.id),
                environment_name=rollout.environment,
                status=AuditStatus.SUCCESS,
                details=redact_dict(
                    {"rollout_key": rollout.rollout_key, "target_percentage": target_pct, "readback": readback}
                ),
            )
        )
        return readback

    @staticmethod
    async def start_rollout(
        session: AsyncSession,
        rollout: ProgressiveRollout,
        *,
        actor_id: uuid.UUID,
        flagsmith_client: Any | None = None,
    ) -> ProgressiveRollout:
        if rollout.status in TERMINAL_ROLLOUT_STATES:
            raise ProgressiveDeliveryError("Rollout is in a terminal state.")

        val = await ProgressiveDeliveryService.validate_rollout(session, rollout, actor_id=actor_id)
        if not val["valid"]:
            raise ProgressiveDeliveryError("; ".join(val["blockers"]))

        stages = list(
            (
                await session.execute(
                    select(ProgressiveRolloutStage)
                    .where(ProgressiveRolloutStage.rollout_id == rollout.id)
                    .order_by(ProgressiveRolloutStage.stage_number)
                )
            )
            .scalars()
            .all()
        )
        stage1 = stages[0]

        if is_production_environment(rollout.environment):
            # Require human authorization before activating Stage 1 in production
            payload = {
                "rollout_id": str(rollout.id),
                "rollout_key": rollout.rollout_key,
                "stage_number": stage1.stage_number,
                "target_percentage": stage1.target_percentage,
                "environment": rollout.environment,
            }
            approval = Approval(
                release_id=rollout.release_id,
                requested_by_id=actor_id,
                policy_category=ApprovalCategory.C,
                action_type="progressive_rollout_activate_stage",
                target_resource_type="progressive_rollout",
                target_resource_id=str(rollout.id),
                environment_name=rollout.environment,
                title=f"Authorize Production Rollout {rollout.rollout_key} Stage 1 ({stage1.target_percentage}%)",
                rationale=f"Production progressive rollout start for {rollout.feature_flag_key}.",
                action_payload=payload,
                expected_precondition={"rollout_version": rollout.version + 1, "current_percentage": 0},
                action_hash=compute_action_hash(
                    "progressive_rollout_activate_stage",
                    "progressive_rollout",
                    str(rollout.id),
                    rollout.environment,
                    payload,
                ),
                idempotency_key=f"rollout:{rollout.id}:s{stage1.stage_number}:v{rollout.version}:{uuid.uuid4().hex[:6]}",
                status=ApprovalStatus.PENDING,
                expires_at=utcnow() + timedelta(hours=24),
            )
            session.add(approval)
            await session.flush()
            stage1.status = ProgressiveStageStatus.AWAITING_APPROVAL
            stage1.approval_id = approval.id
            rollout.current_stage_id = stage1.id
            rollout.status = ProgressiveRolloutStatus.AWAITING_APPROVAL
            rollout.version += 1
            await ProgressiveDeliveryService._record_event(
                session,
                rollout,
                stage_id=stage1.id,
                event_type="approval_requested",
                actor_id=actor_id,
                actor_type=ActorType.USER,
                message=f"Production authorization requested for Stage 1 ({stage1.target_percentage}%).",
                data={"approval_id": str(approval.id)},
            )
            return rollout

        return await ProgressiveDeliveryService._activate_stage(
            session,
            rollout,
            stage1,
            actor_id=actor_id,
            flagsmith_client=flagsmith_client,
        )

    @staticmethod
    async def _activate_stage(
        session: AsyncSession,
        rollout: ProgressiveRollout,
        stage: ProgressiveRolloutStage,
        *,
        actor_id: uuid.UUID | None = None,
        flagsmith_client: Any | None = None,
    ) -> ProgressiveRollout:
        now = utcnow()
        await ProgressiveDeliveryService._apply_flagsmith_stage(
            session,
            rollout,
            stage.target_percentage,
            flagsmith_client=flagsmith_client,
            actor_id=actor_id,
        )
        stage.status = ProgressiveStageStatus.OBSERVING
        stage.activated_at = now
        stage.observation_started_at = now
        stage.observation_ends_at = now + timedelta(seconds=stage.maximum_observation_seconds)
        rollout.current_stage_id = stage.id
        rollout.current_percentage = stage.target_percentage
        rollout.started_at = rollout.started_at or now
        rollout.status = ProgressiveRolloutStatus.OBSERVING
        rollout.version += 1

        if rollout.release_id is not None:
            rel = await session.get(Release, rollout.release_id)
            if rel is not None:
                rel.current_rollout_percentage = stage.target_percentage
                await record_release_event(
                    session,
                    rel,
                    event_type="rollout.stage_activated",
                    stage_key=ReleaseStageKey.ROLLOUT_EXECUTION,
                    actor_id=actor_id,
                    title=f"Rollout {rollout.rollout_key} Stage {stage.stage_number} -> {stage.target_percentage}%",
                )

        await ProgressiveDeliveryService._record_event(
            session,
            rollout,
            stage_id=stage.id,
            event_type="stage_activated",
            actor_id=actor_id,
            actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
            message=f"Activated Stage {stage.stage_number} ({stage.target_percentage}%) in {rollout.environment}.",
        )
        await create_notification(
            session,
            category=NotificationCategory.ROLLOUT_STAGE_ACTIVATED,
            severity="info",
            title=f"Rollout Stage Activated: {rollout.rollout_key} ({stage.target_percentage}%)",
            message=f"{rollout.feature_flag_key} in {rollout.environment} now observing at {stage.target_percentage}%.",
            resource_type="progressive_rollout",
            resource_id=str(rollout.id),
            environment_name=rollout.environment,
            action_href=f"/progressive-delivery/{rollout.id}",
        )
        return rollout

    @staticmethod
    async def evaluate_stage(
        session: AsyncSession,
        rollout: ProgressiveRollout,
        stage: ProgressiveRolloutStage,
        *,
        actor_id: uuid.UUID | None = None,
    ) -> RolloutDecision:
        now = utcnow()
        snapshots = await MetricCollectionService.collect_stage_metrics(session, rollout, stage)
        guardrails = list(
            (
                await session.execute(
                    select(RolloutGuardrail).where(RolloutGuardrail.rollout_id == rollout.id)
                )
            )
            .scalars()
            .all()
        )
        evals = await GuardrailEvaluationService.evaluate_guardrails(
            session, rollout, stage, snapshots, guardrails
        )

        breaches = [e for e in evals if e.status == GuardrailEvalStatus.BREACH]
        stale = [e for e in evals if e.status == GuardrailEvalStatus.STALE_DATA]
        insufficient = [e for e in evals if e.status == GuardrailEvalStatus.INSUFFICIENT_DATA]

        obs_start = ensure_utc(stage.observation_started_at) or now
        elapsed_sec = (now - obs_start).total_seconds()
        req_healthy = int(
            (rollout.observation_configuration or {}).get("required_healthy_evaluations", 2)
        )

        if breaches:
            stage.healthy_evaluations_count = 0
            stage.status = ProgressiveStageStatus.PAUSED
            stage.evaluation_status = "breach"
            stage.decision = RolloutDecisionType.ROLLBACK_RECOMMENDED
            rollout.status = ProgressiveRolloutStatus.ROLLBACK_RECOMMENDED
            rollout.version += 1

            reason = "; ".join(
                f"Guardrail breached (observed={b.observed_value}, threshold={b.threshold})"
                for b in breaches
            )
            dec = RolloutDecision(
                rollout_id=rollout.id,
                stage_id=stage.id,
                decision_type=RolloutDecisionType.ROLLBACK_RECOMMENDED,
                decision_summary=f"Critical guardrail breach detected at {stage.target_percentage}%: {reason}",
                supporting_evidence=[reason],
                recommended_action="Pause rollout and request human-authorized rollback.",
            )
            session.add(dec)
            session.add(
                RollbackOperation(
                    rollout_id=rollout.id,
                    stage_id=stage.id,
                    feature_flag_id=rollout.feature_flag_id,
                    environment=rollout.environment,
                    previous_configuration={"rollout_percentage": stage.target_percentage, "enabled": True},
                    requested_configuration={"rollout_percentage": 0, "enabled": False},
                    status=RollbackOperationStatus.RECOMMENDED,
                    reason=reason,
                    requested_by_id=actor_id,
                )
            )
            await ProgressiveDeliveryService._record_event(
                session,
                rollout,
                stage_id=stage.id,
                event_type="guardrail_breached",
                actor_id=actor_id,
                message=f"Guardrail breach paused progression at {stage.target_percentage}%: {reason}",
            )
            await create_notification(
                session,
                category=NotificationCategory.ROLLOUT_GUARDRAIL_BREACH,
                severity="critical",
                title=f"Guardrail Breach: {rollout.rollout_key} ({stage.target_percentage}%)",
                message=reason,
                resource_type="progressive_rollout",
                resource_id=str(rollout.id),
                environment_name=rollout.environment,
                action_href=f"/progressive-delivery/{rollout.id}",
            )
            await session.flush()
            return dec

        if stale or insufficient or stage.actual_sample_size < stage.minimum_sample_size or elapsed_sec < stage.minimum_observation_seconds:
            missing: list[str] = []
            if stage.actual_sample_size < stage.minimum_sample_size:
                missing.append(
                    f"Sample size {stage.actual_sample_size} < required {stage.minimum_sample_size}"
                )
            if elapsed_sec < stage.minimum_observation_seconds:
                missing.append(
                    f"Observation elapsed {int(elapsed_sec)}s < minimum {stage.minimum_observation_seconds}s"
                )
            if stale:
                missing.append("Telemetry data is stale.")
            dec = RolloutDecision(
                rollout_id=rollout.id,
                stage_id=stage.id,
                decision_type=RolloutDecisionType.CONTINUE_OBSERVATION,
                decision_summary="Continue observation: minimum sample size or observation window not yet satisfied.",
                missing_evidence=missing,
                recommended_action="Continue collecting telemetry before advancing.",
            )
            stage.decision = RolloutDecisionType.CONTINUE_OBSERVATION
            stage.evaluation_status = "observing"
            session.add(dec)
            await session.flush()
            return dec

        stage.healthy_evaluations_count += 1
        if stage.healthy_evaluations_count < req_healthy:
            dec = RolloutDecision(
                rollout_id=rollout.id,
                stage_id=stage.id,
                decision_type=RolloutDecisionType.CONTINUE_OBSERVATION,
                decision_summary=f"Healthy evaluation {stage.healthy_evaluations_count}/{req_healthy} recorded.",
                supporting_evidence=[f"Sample size {stage.actual_sample_size} passed all guardrails."],
                recommended_action="Run one more healthy evaluation before advancing.",
            )
            stage.decision = RolloutDecisionType.CONTINUE_OBSERVATION
            session.add(dec)
            await session.flush()
            return dec

        is_final = stage.target_percentage >= 100
        dec_type = (
            RolloutDecisionType.COMPLETED
            if is_final
            else RolloutDecisionType.ADVANCE_RECOMMENDED
        )
        stage.status = ProgressiveStageStatus.PASSED
        stage.completed_at = now
        stage.evaluation_status = "passed"
        stage.decision = dec_type
        rollout.status = (
            ProgressiveRolloutStatus.COMPLETED
            if is_final
            else ProgressiveRolloutStatus.ADVANCE_RECOMMENDED
        )
        if is_final:
            rollout.completed_at = now
        rollout.version += 1

        dec = RolloutDecision(
            rollout_id=rollout.id,
            stage_id=stage.id,
            decision_type=dec_type,
            decision_summary=(
                f"Stage {stage.stage_number} ({stage.target_percentage}%) passed all guardrails "
                f"with {stage.actual_sample_size} samples."
            ),
            supporting_evidence=[
                f"All {len(evals)} guardrail(s) passed.",
                f"Sample size: {stage.actual_sample_size} >= {stage.minimum_sample_size}.",
            ],
            recommended_action="Rollout completed at 100%." if is_final else "Advance to next stage.",
        )
        session.add(dec)
        await ProgressiveDeliveryService._record_event(
            session,
            rollout,
            stage_id=stage.id,
            event_type="stage_passed",
            actor_id=actor_id,
            message=dec.decision_summary,
        )
        if is_final:
            await create_notification(
                session,
                category=NotificationCategory.ROLLOUT_COMPLETED,
                severity="info",
                title=f"Rollout Completed: {rollout.rollout_key} (100%)",
                message=f"{rollout.feature_flag_key} reached 100% in {rollout.environment}.",
                resource_type="progressive_rollout",
                resource_id=str(rollout.id),
                environment_name=rollout.environment,
                action_href=f"/progressive-delivery/{rollout.id}",
            )
        await session.flush()
        return dec

    @staticmethod
    async def advance_stage(
        session: AsyncSession,
        rollout: ProgressiveRollout,
        *,
        actor_id: uuid.UUID,
        expected_version: int | None = None,
        flagsmith_client: Any | None = None,
    ) -> ProgressiveRollout:
        if expected_version is not None and rollout.version != expected_version:
            raise ProgressiveDeliveryError("Version conflict.", status_code=409, code="version_conflict")
        if rollout.status != ProgressiveRolloutStatus.ADVANCE_RECOMMENDED:
            raise ProgressiveDeliveryError(
                f"Cannot advance rollout from '{rollout.status.value}'; stage must pass evaluation first."
            )
        stages = list(
            (
                await session.execute(
                    select(ProgressiveRolloutStage)
                    .where(ProgressiveRolloutStage.rollout_id == rollout.id)
                    .order_by(ProgressiveRolloutStage.stage_number)
                )
            )
            .scalars()
            .all()
        )
        next_stage = next((s for s in stages if s.status == ProgressiveStageStatus.PENDING), None)
        if next_stage is None:
            rollout.status = ProgressiveRolloutStatus.COMPLETED
            rollout.completed_at = utcnow()
            return rollout

        return await ProgressiveDeliveryService._activate_stage(
            session,
            rollout,
            next_stage,
            actor_id=actor_id,
            flagsmith_client=flagsmith_client,
        )

    @staticmethod
    async def execute_rollback(
        session: AsyncSession,
        rollout: ProgressiveRollout,
        *,
        actor_id: uuid.UUID | None = None,
        reason: str,
        flagsmith_client: Any | None = None,
        has_production_approval: bool = False,
    ) -> RollbackOperation:
        if is_production_environment(rollout.environment) and not has_production_approval:
            raise ProgressiveDeliveryError(
                "Production rollback requires human Approval authorization via PolicyEngine.",
                status_code=403,
                code="production_approval_required",
            )

        now = utcnow()
        readback = await ProgressiveDeliveryService._apply_flagsmith_stage(
            session,
            rollout,
            0,
            flagsmith_client=flagsmith_client,
            actor_id=actor_id,
        )
        op = RollbackOperation(
            rollout_id=rollout.id,
            stage_id=rollout.current_stage_id,
            feature_flag_id=rollout.feature_flag_id,
            environment=rollout.environment,
            previous_configuration={"rollout_percentage": rollout.current_percentage, "enabled": True},
            requested_configuration={"rollout_percentage": 0, "enabled": False},
            status=RollbackOperationStatus.COMPLETED,
            reason=reason,
            requested_by_id=actor_id,
            executed_by_id=actor_id,
            verification_result=readback,
            started_at=now,
            completed_at=now,
        )
        session.add(op)
        rollout.current_percentage = 0
        rollout.status = ProgressiveRolloutStatus.ROLLED_BACK
        rollout.completed_at = now
        rollout.version += 1

        await ProgressiveDeliveryService._record_event(
            session,
            rollout,
            stage_id=rollout.current_stage_id,
            event_type="rolled_back",
            actor_id=actor_id,
            actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
            message=f"Rolled back {rollout.feature_flag_key} in {rollout.environment} to 0%: {reason}",
            data={"verification": readback},
        )
        await session.flush()
        return op
