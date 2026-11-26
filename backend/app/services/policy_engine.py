"""Category A/B/C/FORBIDDEN Policy Engine, single-use locks, drift checks, and audit logging."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
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
    PolicyCategory,
    RolloutAction,
    RolloutHistory,
    WorkspaceSettings,
)

# ------------------------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------------------------


class PolicyError(Exception):
    """Base error for policy and approval lifecycle failures."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class PolicyViolationError(PolicyError):
    """Raised when a FORBIDDEN or unallowlisted action is attempted."""


class ApprovalNotFoundError(PolicyError):
    """Raised when an approval UUID cannot be found."""


class ActionHashMismatchError(PolicyError):
    """Raised when stored action_hash does not match recomputed canonical SHA-256."""


class ApprovalExpiredError(PolicyError):
    """Raised when an approval has exceeded its expires_at TTL."""


class ApprovalConflictError(PolicyError):
    """Raised when single-use atomic lock fails or approval is no longer pending."""


class PreconditionDriftError(PolicyError):
    """Raised when live external state drifted from expected_precondition."""


class VerificationFailedError(PolicyError):
    """Raised when post-mutation readback fails target state invariants."""


# ------------------------------------------------------------------------------
# Policy Classification Tables
# ------------------------------------------------------------------------------

_PROD_ENV_NAMES = frozenset({"prod", "production", "live", "prd"})
_NON_PROD_ENV_NAMES = frozenset(
    {
        "dev",
        "development",
        "staging",
        "stage",
        "qa",
        "test",
        "testing",
        "local",
        "preview",
        "sandbox",
        "uat",
    }
)

FORBIDDEN_ACTIONS = frozenset(
    {
        "github.merge_pr",
        "github_merge_pull_request",
        "github.force_push",
        "github.push_main",
        "policy.bypass_hash",
        "fs.modify_host",
    }
)

CATEGORY_A_ACTIONS = frozenset(
    {
        # pr_agent reads/analysis/validation
        "github.list_repositories",
        "github_list_repositories",
        "github.list_branches",
        "github_list_branches",
        "github.list_pull_requests",
        "github_list_pull_requests",
        "github.get_pull_request_diff",
        "github_get_pull_request_diff",
        "github.get_file_contents",
        "github_get_file_contents",
        "github.search_repo_code",
        "github_search_repo_code",
        "pr.analyze",
        "analyze_pr_risk_and_opportunities",
        "patch.generate_wrap",
        "generate_flag_wrapping_patch",
        "patch.validate_isolated",
        "validate_patch_isolated",
        "patch.generate_cleanup",
        "generate_flag_cleanup_patch",
        # flag_agent reads/verification/health
        "flagsmith.list_projects",
        "flagsmith_list_projects",
        "flagsmith.list_environments",
        "flagsmith_list_environments",
        "flagsmith.list_flags",
        "flagsmith_list_flags",
        "flagsmith.get_flag_state",
        "flagsmith_get_flag_state",
        "flagsmith.verify_live_state",
        "flagsmith_verify_live_state",
        "flag_health.scan",
        "scan_flag_health",
        # experiment_agent reads/analysis/planning/proposing
        "flagsmith.probe_experiment_capability",
        "flagsmith_probe_experiment_capability",
        "experiment.plan",
        "create_experiment_plan",
        "experiment.fetch_events",
        "fetch_experiment_events_summary",
        "experiment.bayesian_analysis",
        "compute_bayesian_experiment_analysis",
        "experiment.recommendation",
        "evaluate_release_recommendation",
        "incident.investigate",
        "investigate_release_incident",
        "rollout.timeline",
        "get_rollout_timeline",
        "release.propose_action",
        "propose_release_action",
    }
)

ALWAYS_CATEGORY_C_ACTIONS = frozenset(
    {
        "flagsmith.delete_flag",
        "flagsmith.archive_flag",
        "flagsmith.delete_or_archive_flag",
        "flagsmith_delete_or_archive_flag",
        "promote_experiment_winner",
        "experiment_promote_winner",
    }
)

ALWAYS_CATEGORY_B_ACTIONS = frozenset(
    {
        "github.create_branch_and_draft_pr",
        "github_create_branch_and_draft_pr",
        "flagsmith.create_flag",
        "flagsmith_create_flag",
        "flagsmith.upsert_percentage_segment",
        "flagsmith_upsert_percentage_segment",
    }
)

ENV_SCOPED_MUTATIONS = frozenset(
    {
        "flagsmith.toggle_flag",
        "flagsmith_toggle_flag",
        "flagsmith.set_gradual_rollout_stage",
        "flagsmith_set_gradual_rollout_stage",
        "flagsmith.configure_multivariate_split",
        "flagsmith_configure_multivariate_split",
        "flagsmith.rollback_flag",
        "flagsmith_rollback_flag",
        "flagsmith.publish_version",
        "experiment.activate",
        "activate_experiment",
        "experiment.stop",
        "stop_or_conclude_experiment",
        "release.rollout_approve",
        "release_rollout_approve",
        "progressive_rollout.activate_stage",
        "progressive_rollout_activate_stage",
        "progressive_rollout.rollback",
        "progressive_rollout_rollback",
    }
)


def is_production_environment(
    environment_name: str | None,
    env_states: dict[str, Any] | None = None,
) -> bool:
    """Fail-closed production environment classification per docs/database-and-policies.md Sec 5.1.

    Returns True when:
    - environment_name is missing/empty (fail closed)
    - normalized name matches prod/production/live/prd or contains 'prod'
    - env_states metadata marks is_production == True
    - environment is unknown/unrecognized without explicit is_production == False metadata
    """
    if not environment_name or not environment_name.strip():
        return True

    env_norm = environment_name.strip().lower()
    if env_norm in _PROD_ENV_NAMES or "prod" in env_norm:
        return True

    if env_states:
        matched_meta: dict[str, Any] | None = None
        if environment_name in env_states and isinstance(env_states[environment_name], dict):
            matched_meta = env_states[environment_name]
        else:
            for k, v in env_states.items():
                if k.strip().lower() == env_norm and isinstance(v, dict):
                    matched_meta = v
                    break

        if matched_meta is not None and "is_production" in matched_meta:
            return bool(matched_meta["is_production"])

    # Unknown or ambiguous environment fails closed as production (Category C).
    return env_norm not in _NON_PROD_ENV_NAMES


def classify_action(
    action_type: str,
    environment_name: str | None = None,
    env_states: dict[str, Any] | None = None,
) -> PolicyCategory:
    """Classify an action into Category A, B, C, or DENY (FORBIDDEN)."""
    normalized = action_type.strip()
    if normalized in FORBIDDEN_ACTIONS:
        return PolicyCategory.DENY
    if normalized in CATEGORY_A_ACTIONS:
        return PolicyCategory.A
    if normalized in ALWAYS_CATEGORY_C_ACTIONS:
        return PolicyCategory.C
    if normalized in ALWAYS_CATEGORY_B_ACTIONS:
        return PolicyCategory.B
    if normalized in ENV_SCOPED_MUTATIONS:
        return (
            PolicyCategory.C
            if is_production_environment(environment_name, env_states)
            else PolicyCategory.B
        )
    # Fail closed on unrecognized actions.
    return PolicyCategory.DENY


RevalidatorFn = Callable[[Approval], Awaitable[dict[str, Any]]]
ExecutorFn = Callable[[Approval], Awaitable[dict[str, Any]]]
PostVerifierFn = Callable[[Approval, dict[str, Any]], Awaitable[dict[str, Any]]]


class PolicyEngine:
    """Enforces Category A/B/C policy gates, hash verification, single-use locks, and auditing."""

    @staticmethod
    async def get_workspace_settings(session: AsyncSession) -> WorkspaceSettings:
        """Load singleton WorkspaceSettings, creating default row if absent."""
        stmt = select(WorkspaceSettings).where(WorkspaceSettings.singleton_key == "default")
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            return row

        cfg = get_settings()
        row = WorkspaceSettings(
            singleton_key="default",
            category_b_requires_confirmation=cfg.CATEGORY_B_REQUIRES_CONFIRMATION,
            approval_ttl_seconds=cfg.APPROVAL_TTL_SECONDS,
            gemini_model=cfg.GEMINI_MODEL,
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def record_audit_log(
        session: AsyncSession,
        *,
        actor_type: ActorType,
        actor_id: str | None,
        policy_category: PolicyCategory,
        event_action: str,
        resource_type: str,
        resource_id: str | None = None,
        environment_name: str | None = None,
        approval_id: uuid.UUID | None = None,
        action_hash: str | None = None,
        status: AuditStatus,
        details: dict[str, Any] | None = None,
    ) -> AuditLog:
        """Append a secret-redacted compliance entry to audit_logs."""
        entry = AuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            policy_category=policy_category,
            event_action=event_action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            environment_name=environment_name,
            approval_id=approval_id,
            action_hash=action_hash,
            status=status,
            details=redact_dict(details or {}),
        )
        session.add(entry)
        await session.flush()
        return entry

    @staticmethod
    async def record_rollout_history(
        session: AsyncSession,
        *,
        feature_flag_id: uuid.UUID,
        environment_name: str,
        action: RolloutAction,
        previous_state: dict[str, Any],
        new_state: dict[str, Any],
        verified: bool,
        verification_details: dict[str, Any] | None = None,
        approval_id: uuid.UUID | None = None,
        actor_id: uuid.UUID | None = None,
        reason: str | None = None,
    ) -> RolloutHistory:
        """Record an immutable rollout_history transition."""
        entry = RolloutHistory(
            feature_flag_id=feature_flag_id,
            approval_id=approval_id,
            actor_id=actor_id,
            environment_name=environment_name,
            action=action,
            previous_state=redact_dict(previous_state),
            new_state=redact_dict(new_state),
            verified=verified,
            verification_details=redact_dict(verification_details or {}),
            reason=reason,
        )
        session.add(entry)
        await session.flush()
        return entry

    @classmethod
    async def evaluate_action(
        cls,
        session: AsyncSession,
        *,
        action_type: str,
        environment_name: str | None = None,
        env_states: dict[str, Any] | None = None,
        category_b_requires_confirmation: bool | None = None,
        actor_type: ActorType = ActorType.SYSTEM,
        actor_id: str | None = None,
        resource_type: str = "system",
        resource_id: str | None = None,
    ) -> tuple[PolicyCategory, bool]:
        """Classify action and return (policy_category, requires_approval).

        Raises PolicyViolationError and audits DENY for FORBIDDEN actions.
        """
        category = classify_action(
            action_type, environment_name=environment_name, env_states=env_states
        )
        if category == PolicyCategory.DENY:
            await cls.record_audit_log(
                session,
                actor_type=actor_type,
                actor_id=actor_id,
                policy_category=PolicyCategory.DENY,
                event_action="policy.blocked_forbidden",
                resource_type=resource_type,
                resource_id=resource_id,
                environment_name=environment_name,
                status=AuditStatus.BLOCKED,
                details={"action_type": action_type},
            )
            raise PolicyViolationError(
                f"Action '{action_type}' is forbidden by ReleaseGuard policy.",
                details={"action_type": action_type, "policy_category": "DENY"},
            )

        if category == PolicyCategory.A:
            return PolicyCategory.A, False

        if category == PolicyCategory.C:
            return PolicyCategory.C, True

        if category_b_requires_confirmation is None:
            ws = await cls.get_workspace_settings(session)
            category_b_requires_confirmation = bool(ws.category_b_requires_confirmation)

        return PolicyCategory.B, bool(category_b_requires_confirmation)

    @classmethod
    async def create_approval(
        cls,
        session: AsyncSession,
        *,
        action_type: str,
        target_resource_type: str,
        target_resource_id: str,
        environment_name: str | None,
        title: str,
        action_payload: dict[str, Any],
        expected_precondition: dict[str, Any] | None = None,
        rationale: str | None = None,
        env_states: dict[str, Any] | None = None,
        agent_task_id: uuid.UUID | None = None,
        requested_by_id: uuid.UUID | None = None,
        idempotency_key: str | None = None,
        ttl_seconds: int | None = None,
    ) -> Approval:
        """Create a SHA-256 hash-bound, TTL-bounded pending Approval row."""
        category = classify_action(
            action_type, environment_name=environment_name, env_states=env_states
        )
        if category in (PolicyCategory.DENY, PolicyCategory.A):
            raise PolicyViolationError(
                f"Cannot create approval for Category {category.value} action '{action_type}'."
            )

        approval_cat = (
            ApprovalCategory.C if category == PolicyCategory.C else ApprovalCategory.B
        )

        ws = await cls.get_workspace_settings(session)
        effective_ttl = ttl_seconds if ttl_seconds is not None else ws.approval_ttl_seconds
        now = utcnow()
        expires_at = now + timedelta(seconds=effective_ttl)

        action_hash = compute_action_hash(
            action_type=action_type,
            target_resource_type=target_resource_type,
            target_resource_id=str(target_resource_id),
            environment_name=environment_name,
            action_payload=action_payload,
        )
        idem_key = idempotency_key or f"{action_type}:{action_hash[:16]}:{uuid.uuid4().hex[:12]}"

        approval = Approval(
            agent_task_id=agent_task_id,
            requested_by_id=requested_by_id,
            policy_category=approval_cat,
            action_type=action_type,
            target_resource_type=target_resource_type,
            target_resource_id=str(target_resource_id),
            environment_name=environment_name,
            title=title,
            rationale=rationale,
            action_payload=action_payload,
            expected_precondition=expected_precondition or {},
            action_hash=action_hash,
            idempotency_key=idem_key,
            status=ApprovalStatus.PENDING,
            expires_at=expires_at,
        )
        session.add(approval)
        await session.flush()

        await cls.record_audit_log(
            session,
            actor_type=ActorType.AGENT if agent_task_id else ActorType.USER,
            actor_id=str(requested_by_id) if requested_by_id else None,
            policy_category=category,
            event_action="approval.created",
            resource_type=target_resource_type,
            resource_id=str(target_resource_id),
            environment_name=environment_name,
            approval_id=approval.id,
            action_hash=action_hash,
            status=AuditStatus.PENDING,
            details={"action_type": action_type, "expires_at": expires_at.isoformat()},
        )
        from app.models import NotificationCategory
        from app.services.notification_service import create_notification

        await create_notification(
            session,
            category=NotificationCategory.APPROVAL_REQUIRED,
            severity="critical" if approval_cat == ApprovalCategory.C else "high",
            title=f"Approval Required (Cat {approval_cat.value}): {title[:180]}",
            message=rationale or f"Action `{action_type}` waiting for operator approval.",
            resource_type=target_resource_type,
            resource_id=str(target_resource_id),
            environment_name=environment_name,
            action_href="/approvals",
            metadata={"approval_id": str(approval.id), "action_type": action_type},
        )
        return approval

    @classmethod
    async def reject_approval(
        cls,
        session: AsyncSession,
        approval_id: uuid.UUID,
        *,
        decided_by_id: uuid.UUID | None = None,
        comment: str,
    ) -> Approval:
        """Atomically transition a pending approval to rejected and write an audit log."""
        now = utcnow()
        existing = await session.get(Approval, approval_id)
        if existing is None:
            raise ApprovalNotFoundError(f"Approval {approval_id} not found.")

        stmt = (
            update(Approval)
            .where(Approval.id == approval_id, Approval.status == ApprovalStatus.PENDING)
            .values(
                status=ApprovalStatus.REJECTED,
                decided_by_id=decided_by_id,
                decided_at=now,
                decision_comment=comment,
                updated_at=now,
            )
        )
        result = await session.execute(stmt)
        if result.rowcount != 1:
            raise ApprovalConflictError(
                f"Approval {approval_id} cannot be rejected (current status: {existing.status})."
            )

        await session.refresh(existing)
        await cls.record_audit_log(
            session,
            actor_type=ActorType.USER,
            actor_id=str(decided_by_id) if decided_by_id else None,
            policy_category=PolicyCategory(existing.policy_category.value),
            event_action="approval.rejected",
            resource_type=existing.target_resource_type,
            resource_id=existing.target_resource_id,
            environment_name=existing.environment_name,
            approval_id=existing.id,
            action_hash=existing.action_hash,
            status=AuditStatus.BLOCKED,
            details={"comment": comment},
        )
        await session.commit()
        await session.refresh(existing)
        return existing

    @classmethod
    async def execute_approved_action(
        cls,
        session: AsyncSession,
        approval_id: uuid.UUID,
        *,
        decided_by_id: uuid.UUID | None = None,
        comment: str | None = None,
        revalidator: RevalidatorFn,
        executor: ExecutorFn,
        post_verifier: PostVerifierFn | None = None,
        rollout_recorder_context: dict[str, Any] | None = None,
    ) -> Approval:
        """Execute an approval following the exact 5-step protocol in docs/database-and-policies.md Sec 5.4:

        1. Hash & TTL Verification
        2. Atomic Single-Use Conditional UPDATE Lock (pending -> executing)
        3. Live-State Precondition Revalidation (drift -> revalidation_failed)
        4. External Mutation Execution (exactly once)
        5. Post-Mutation State Verification + rollout_history & audit_logs
        """
        approval = await session.get(Approval, approval_id)
        if approval is None:
            raise ApprovalNotFoundError(f"Approval {approval_id} not found.")

        policy_cat = PolicyCategory(approval.policy_category.value)

        # Step 1a: Verify canonical SHA-256 action_hash integrity
        computed_hash = compute_action_hash(
            action_type=approval.action_type,
            target_resource_type=approval.target_resource_type,
            target_resource_id=approval.target_resource_id,
            environment_name=approval.environment_name,
            action_payload=approval.action_payload,
        )
        if computed_hash != approval.action_hash:
            await cls.record_audit_log(
                session,
                actor_type=ActorType.USER,
                actor_id=str(decided_by_id) if decided_by_id else None,
                policy_category=PolicyCategory.DENY,
                event_action="policy.action_hash_tampered",
                resource_type=approval.target_resource_type,
                resource_id=approval.target_resource_id,
                environment_name=approval.environment_name,
                approval_id=approval.id,
                action_hash=computed_hash,
                status=AuditStatus.BLOCKED,
                details={
                    "stored_hash": approval.action_hash,
                    "computed_hash": computed_hash,
                },
            )
            await session.commit()
            raise ActionHashMismatchError(
                "Approval action_hash mismatch: payload or metadata was altered.",
                details={"stored_hash": approval.action_hash, "computed_hash": computed_hash},
            )

        # Step 1b: Check status & TTL expiration
        if approval.status != ApprovalStatus.PENDING:
            raise ApprovalConflictError(
                f"Approval {approval_id} is already in state '{approval.status}'."
            )

        now = utcnow()
        expires_at = ensure_utc(approval.expires_at)
        if expires_at is not None and now > expires_at:
            expire_stmt = (
                update(Approval)
                .where(Approval.id == approval_id, Approval.status == ApprovalStatus.PENDING)
                .values(status=ApprovalStatus.EXPIRED, updated_at=now)
            )
            await session.execute(expire_stmt)
            await cls.record_audit_log(
                session,
                actor_type=ActorType.SYSTEM,
                actor_id=str(decided_by_id) if decided_by_id else None,
                policy_category=policy_cat,
                event_action="approval.expired",
                resource_type=approval.target_resource_type,
                resource_id=approval.target_resource_id,
                environment_name=approval.environment_name,
                approval_id=approval.id,
                action_hash=approval.action_hash,
                status=AuditStatus.FAILED,
                details={"expires_at": expires_at.isoformat()},
            )
            await session.commit()
            raise ApprovalExpiredError(
                f"Approval {approval_id} expired at {expires_at.isoformat()}."
            )

        # Step 2: Atomic single-use conditional lock (pending -> executing)
        lock_stmt = (
            update(Approval)
            .where(
                Approval.id == approval_id,
                Approval.status == ApprovalStatus.PENDING,
                Approval.expires_at > now,
            )
            .values(
                status=ApprovalStatus.EXECUTING,
                decided_by_id=decided_by_id,
                decided_at=now,
                decision_comment=comment,
                updated_at=now,
            )
        )
        lock_result = await session.execute(lock_stmt)
        if lock_result.rowcount != 1:
            await session.rollback()
            raise ApprovalConflictError(
                f"Failed to acquire single-use execution lock for approval {approval_id}."
            )
        await session.commit()
        await session.refresh(approval)

        # Step 3: Live-state precondition revalidation (fail closed on any probe error)
        try:
            reval = await revalidator(approval)
        except Exception as exc:  # noqa: BLE001
            reval = {"ok": False, "details": {"error": scrub_secrets(str(exc))}}

        if not bool(reval.get("ok", False)):
            drift_details = redact_dict(reval.get("details") or reval)
            approval.status = ApprovalStatus.REVALIDATION_FAILED
            approval.execution_result = {"drift": drift_details}
            approval.updated_at = utcnow()
            await cls.record_audit_log(
                session,
                actor_type=ActorType.USER,
                actor_id=str(decided_by_id) if decided_by_id else None,
                policy_category=policy_cat,
                event_action="approval.revalidation_failed",
                resource_type=approval.target_resource_type,
                resource_id=approval.target_resource_id,
                environment_name=approval.environment_name,
                approval_id=approval.id,
                action_hash=approval.action_hash,
                status=AuditStatus.FAILED,
                details=drift_details,
            )
            await session.commit()
            raise PreconditionDriftError(
                "Live external state drifted since approval was requested.",
                details=drift_details,
            )

        # Step 4: Execute external mutation exactly once
        try:
            exec_result = await executor(approval)
        except Exception as exc:
            err_msg = scrub_secrets(str(exc))
            approval.status = ApprovalStatus.FAILED
            approval.execution_result = {"error": err_msg}
            approval.updated_at = utcnow()
            await cls.record_audit_log(
                session,
                actor_type=ActorType.USER,
                actor_id=str(decided_by_id) if decided_by_id else None,
                policy_category=policy_cat,
                event_action="approval.execution_failed",
                resource_type=approval.target_resource_type,
                resource_id=approval.target_resource_id,
                environment_name=approval.environment_name,
                approval_id=approval.id,
                action_hash=approval.action_hash,
                status=AuditStatus.FAILED,
                details={"error": err_msg, "action_type": approval.action_type},
            )
            await session.commit()
            raise

        # Step 5: Post-mutation live state verification
        if post_verifier is not None:
            verify_res = await post_verifier(approval, exec_result)
            if not bool(verify_res.get("matches", False)):
                safe_verify = redact_dict(verify_res)
                approval.status = ApprovalStatus.FAILED
                approval.execution_result = {
                    "execution": redact_dict(exec_result),
                    "verification": safe_verify,
                }
                approval.updated_at = utcnow()
                if rollout_recorder_context and rollout_recorder_context.get("feature_flag_id"):
                    await cls.record_rollout_history(
                        session,
                        feature_flag_id=rollout_recorder_context["feature_flag_id"],
                        environment_name=approval.environment_name or "production",
                        action=rollout_recorder_context.get("action", RolloutAction.ROLLOUT_STEP),
                        previous_state=rollout_recorder_context.get(
                            "previous_state", approval.expected_precondition
                        ),
                        new_state=rollout_recorder_context.get(
                            "new_state", approval.action_payload
                        ),
                        verified=False,
                        verification_details=safe_verify,
                        approval_id=approval.id,
                        actor_id=decided_by_id,
                        reason=comment or approval.rationale,
                    )
                await cls.record_audit_log(
                    session,
                    actor_type=ActorType.SYSTEM,
                    actor_id=str(decided_by_id) if decided_by_id else None,
                    policy_category=policy_cat,
                    event_action="flagsmith.verification_failed",
                    resource_type=approval.target_resource_type,
                    resource_id=approval.target_resource_id,
                    environment_name=approval.environment_name,
                    approval_id=approval.id,
                    action_hash=approval.action_hash,
                    status=AuditStatus.FAILED,
                    details=safe_verify,
                )
                await session.commit()
                raise VerificationFailedError(
                    "Post-mutation readback did not match expected state.",
                    details=safe_verify,
                )
        else:
            verify_res = {"matches": True}

        done_at = utcnow()
        safe_exec = redact_dict(exec_result)
        approval.status = ApprovalStatus.EXECUTED
        approval.executed_at = done_at
        approval.updated_at = done_at
        approval.execution_result = {
            "execution": safe_exec,
            "verification": redact_dict(verify_res),
        }

        if rollout_recorder_context and rollout_recorder_context.get("feature_flag_id"):
            await cls.record_rollout_history(
                session,
                feature_flag_id=rollout_recorder_context["feature_flag_id"],
                environment_name=approval.environment_name or "production",
                action=rollout_recorder_context.get("action", RolloutAction.ROLLOUT_STEP),
                previous_state=rollout_recorder_context.get(
                    "previous_state", approval.expected_precondition
                ),
                new_state=rollout_recorder_context.get("new_state", approval.action_payload),
                verified=True,
                verification_details=redact_dict(verify_res),
                approval_id=approval.id,
                actor_id=decided_by_id,
                reason=comment or approval.rationale,
            )

        await cls.record_audit_log(
            session,
            actor_type=ActorType.USER,
            actor_id=str(decided_by_id) if decided_by_id else None,
            policy_category=policy_cat,
            event_action="approval.executed",
            resource_type=approval.target_resource_type,
            resource_id=approval.target_resource_id,
            environment_name=approval.environment_name,
            approval_id=approval.id,
            action_hash=approval.action_hash,
            status=AuditStatus.SUCCESS,
            details={"action_type": approval.action_type, "result": safe_exec},
        )
        await session.commit()
        await session.refresh(approval)
        return approval
