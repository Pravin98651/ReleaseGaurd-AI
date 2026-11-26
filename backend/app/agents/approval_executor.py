"""Approval precondition capture, drift revalidation, and single-use execution bridge."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.agents.tools.context import ToolExecutionContext, _extract_pct_from_seg
from app.agents.tools.experiment_tools import (
    handle_activate_experiment,
    handle_promote_experiment_winner,
    handle_stop_or_conclude_experiment,
)
from app.agents.tools.flag_tools import (
    handle_flagsmith_configure_multivariate_split,
    handle_flagsmith_create_flag,
    handle_flagsmith_delete_or_archive_flag,
    handle_flagsmith_rollback_flag,
    handle_flagsmith_set_gradual_rollout_stage,
    handle_flagsmith_toggle_flag,
    handle_flagsmith_upsert_percentage_segment,
)
from app.agents.tools.pr_tools import handle_github_create_branch_and_draft_pr
from app.models import (
    Approval,
    CodeChange,
    Experiment,
    FeatureFlag,
    ProgressiveRollout,
    ProgressiveRolloutStage,
    ProgressiveStageStatus,
    RolloutAction,
    ValidationStatus,
)
from app.schemas.tools import (
    ActivateExperimentInput,
    FlagsmithConfigureMultivariateSplitInput,
    FlagsmithCreateFlagInput,
    FlagsmithDeleteOrArchiveFlagInput,
    FlagsmithRollbackFlagInput,
    FlagsmithSetGradualRolloutStageInput,
    FlagsmithToggleFlagInput,
    FlagsmithUpsertPercentageSegmentInput,
    GitHubCreateBranchAndDraftPRInput,
    StopOrConcludeExperimentInput,
)
from app.services.policy_engine import PolicyEngine


def _normalize_action(action_type: str) -> str:
    return action_type.strip().replace(".", "_")


async def _snapshot_flag_live_state(
    ctx: ToolExecutionContext,
    flag: FeatureFlag,
    environment_name: str,
) -> dict[str, Any]:
    env_norm = environment_name.strip().lower()
    cached = dict((flag.env_states or {}).get(env_norm) or {})
    snapshot: dict[str, Any] = {
        "flag_key": flag.key,
        "environment_name": env_norm,
        "enabled": bool(cached.get("enabled", False)),
        "rollout_percentage": int(cached.get("rollout_percentage") or 0),
        "is_archived": bool(flag.is_archived),
    }
    if cached.get("version_uuid"):
        snapshot["version_uuid"] = cached["version_uuid"]

    try:
        client = await ctx.get_flagsmith_client()
        if flag.flagsmith_feature_id is not None:
            env_key = await ctx.resolve_env_key(
                env_norm, flag=flag, project_id=flag.flagsmith_project_id
            )
            states = await client.get_environment_feature_states(
                env_key, feature_id=int(flag.flagsmith_feature_id)
            )
            base_fs, _ = client._partition_feature_states(states)
            live = await client.verify_live_state(
                env_key=env_key,
                feature_id=int(flag.flagsmith_feature_id),
                flag_key=flag.key,
                project_id=flag.flagsmith_project_id,
            )
            actual = live.get("actual_state") or {}
            snapshot["enabled"] = bool(actual.get("enabled", snapshot["enabled"]))
            if actual.get("rollout_percentage") is not None:
                snapshot["rollout_percentage"] = int(actual["rollout_percentage"])
            elif base_fs is not None:
                # Check if any segment override is enabled when base is False
                segments = await client.list_segments(flag.flagsmith_project_id)
                seg_map = {int(s["id"]): s for s in segments if "id" in s}
                rollout_seg_name = f"rg_rollout_{flag.key}"
                matched_pct = 100 if snapshot["enabled"] else 0
                for fs in states:
                    feat_seg = fs.get("feature_segment")
                    if feat_seg is None or not fs.get("enabled"):
                        continue
                    seg_id = (
                        feat_seg.get("segment") if isinstance(feat_seg, dict) else feat_seg
                    )
                    seg_obj = seg_map.get(int(seg_id)) if seg_id is not None else None
                    if seg_obj and seg_obj.get("name") == rollout_seg_name:
                        extracted = _extract_pct_from_seg(seg_obj)
                        if extracted is not None:
                            matched_pct = extracted
                snapshot["rollout_percentage"] = matched_pct
            if base_fs and base_fs.get("version_uuid"):
                snapshot["version_uuid"] = base_fs["version_uuid"]
    except ValueError:
        pass
    return snapshot


async def capture_expected_precondition(
    ctx: ToolExecutionContext,
    *,
    action_type: str,
    action_payload: dict[str, Any],
) -> tuple[str, str, dict[str, Any], dict[str, Any] | None]:
    """Capture live precondition snapshot before creating a Category B/C Approval."""
    norm = _normalize_action(action_type)

    if norm == "github_create_branch_and_draft_pr":
        change_id = uuid.UUID(str(action_payload["code_change_id"]))
        change = await ctx.session.get(CodeChange, change_id)
        if change is None:
            raise ValueError(f"CodeChange {change_id} not found.")
        return (
            "code_change",
            str(change.id),
            {
                "code_change_id": str(change.id),
                "validation_status": change.validation_status.value,
                "draft_pr_number": change.draft_pr_number,
            },
            None,
        )

    if norm == "flagsmith_create_flag":
        pid = int(action_payload["project_id"])
        key = str(action_payload["key"])
        exists = False
        try:
            client = await ctx.get_flagsmith_client()
            exists = (await client.find_flag_by_key(pid, key)) is not None
        except ValueError:
            pass
        return (
            "feature_flag",
            key,
            {"project_id": pid, "key": key, "exists": exists},
            None,
        )

    if norm == "flagsmith_upsert_percentage_segment":
        pid = int(action_payload["project_id"])
        seg_name = str(action_payload["segment_name"])
        return (
            "flagsmith_segment",
            seg_name,
            {"project_id": pid, "segment_name": seg_name},
            None,
        )

    if norm in (
        "flagsmith_toggle_flag",
        "flagsmith_set_gradual_rollout_stage",
        "flagsmith_configure_multivariate_split",
        "flagsmith_rollback_flag",
        "flagsmith_delete_flag",
        "flagsmith_archive_flag",
        "flagsmith_delete_or_archive_flag",
    ):
        flag_key = str(action_payload["flag_key"])
        env_name = str(action_payload.get("environment_name") or "production").strip().lower()
        pid = await ctx.resolve_flagsmith_project_id()
        flag = await ctx.find_feature_flag(flag_key=flag_key, project_id=pid)
        if flag is None and pid is not None:
            try:
                flag = await ctx.sync_flag_from_flagsmith(flag_key, project_id=pid)
            except Exception:  # noqa: BLE001
                flag = None
        if flag is None:
            return (
                "feature_flag",
                flag_key,
                {"flag_key": flag_key, "environment_name": env_name},
                None,
            )
        snapshot = await _snapshot_flag_live_state(ctx, flag, env_name)
        return (
            "feature_flag",
            str(flag.id),
            snapshot,
            dict(flag.env_states or {}),
        )

    if norm in (
        "experiment_activate",
        "activate_experiment",
        "experiment_stop",
        "stop_or_conclude_experiment",
        "promote_experiment_winner",
        "experiment_promote_winner",
    ):
        exp_id = uuid.UUID(str(action_payload["experiment_id"]))
        exp = await ctx.session.get(Experiment, exp_id)
        if exp is None:
            raise ValueError(f"Experiment {exp_id} not found.")
        flag = await ctx.session.get(FeatureFlag, exp.feature_flag_id)
        precond: dict[str, Any] = {
            "experiment_id": str(exp.id),
            "status": exp.status.value,
            "environment_name": exp.environment_name,
        }
        env_states: dict[str, Any] | None = None
        if flag is not None:
            env_states = dict(flag.env_states or {})
            flag_snap = await _snapshot_flag_live_state(ctx, flag, exp.environment_name)
            precond["flag_enabled"] = flag_snap["enabled"]
            precond["flag_rollout_percentage"] = flag_snap["rollout_percentage"]
        return ("experiment", str(exp.id), precond, env_states)

    if norm in ("progressive_rollout_activate_stage", "progressive_rollout_rollback"):
        rollout_id = uuid.UUID(str(action_payload["rollout_id"]))
        rollout = await ctx.session.get(ProgressiveRollout, rollout_id)
        if rollout is None:
            raise ValueError(f"ProgressiveRollout {rollout_id} not found.")
        flag = await ctx.session.get(FeatureFlag, rollout.feature_flag_id)
        precond = {
            "rollout_id": str(rollout.id),
            "rollout_version": rollout.version,
            "current_percentage": rollout.current_percentage,
            "status": rollout.status.value,
            "environment": rollout.environment,
        }
        env_states = dict(flag.env_states or {}) if flag is not None else None
        return ("progressive_rollout", str(rollout.id), precond, env_states)

    raise ValueError(f"Unsupported approval action_type '{action_type}'.")


async def _revalidate_flag_state(
    ctx: ToolExecutionContext,
    approval: Approval,
) -> dict[str, Any]:
    pre = dict(approval.expected_precondition or {})
    flag_key = str(pre.get("flag_key") or approval.action_payload.get("flag_key") or "")
    env_name = str(
        approval.environment_name
        or pre.get("environment_name")
        or approval.action_payload.get("environment_name")
        or "production"
    )
    flag: FeatureFlag | None = None
    try:
        flag = await ctx.session.get(FeatureFlag, uuid.UUID(approval.target_resource_id))
    except ValueError:
        flag = None
    if flag is None and flag_key:
        flag = await ctx.find_feature_flag(flag_key=flag_key)

    if flag is None or flag.flagsmith_feature_id is None:
        return {"ok": True, "details": {"note": "Local DB record verified without remote ID."}}

    client = await ctx.get_flagsmith_client()
    env_key = await ctx.resolve_env_key(
        env_name, flag=flag, project_id=flag.flagsmith_project_id
    )
    states = await client.get_environment_feature_states(
        env_key, feature_id=int(flag.flagsmith_feature_id)
    )
    base_fs, _ = client._partition_feature_states(states)
    live = await client.verify_live_state(
        env_key=env_key,
        feature_id=int(flag.flagsmith_feature_id),
        flag_key=flag.key,
        project_id=flag.flagsmith_project_id,
        expected_enabled=pre.get("enabled") if "enabled" in pre else None,
        expected_percentage=pre.get("rollout_percentage") if "rollout_percentage" in pre else None,
    )
    actual = dict(live.get("actual_state") or {})
    if base_fs and base_fs.get("version_uuid"):
        actual["version_uuid"] = base_fs["version_uuid"]

    mismatches = list(live.get("mismatches") or [])
    if pre.get("version_uuid") and actual.get("version_uuid") and pre["version_uuid"] != actual["version_uuid"]:
        mismatches.append(
            f"Expected version_uuid={pre['version_uuid']}, got {actual['version_uuid']}."
        )
    if "is_archived" in pre and bool(flag.is_archived) != bool(pre["is_archived"]):
        mismatches.append(
            f"Expected is_archived={pre['is_archived']}, got {flag.is_archived}."
        )

    if mismatches:
        return {
            "ok": False,
            "details": {
                "expected": pre,
                "actual": actual,
                "mismatches": mismatches,
            },
        }
    return {"ok": True, "details": {"actual": actual}}


async def _run_revalidation(
    ctx: ToolExecutionContext,
    approval: Approval,
) -> dict[str, Any]:
    norm = _normalize_action(approval.action_type)
    pre = dict(approval.expected_precondition or {})

    if norm == "github_create_branch_and_draft_pr":
        change = await ctx.session.get(
            CodeChange, uuid.UUID(str(approval.action_payload["code_change_id"]))
        )
        if change is None:
            return {"ok": False, "details": {"error": "CodeChange no longer exists."}}
        if change.validation_status != ValidationStatus.PASSED:
            return {
                "ok": False,
                "details": {
                    "expected_validation_status": "passed",
                    "actual_validation_status": change.validation_status.value,
                },
            }
        if change.draft_pr_number is not None:
            return {
                "ok": False,
                "details": {
                    "error": "Draft PR already exists for this CodeChange.",
                    "draft_pr_number": change.draft_pr_number,
                },
            }
        return {"ok": True, "details": {"validation_status": "passed"}}

    if norm == "flagsmith_create_flag":
        if pre.get("exists") is False:
            try:
                client = await ctx.get_flagsmith_client()
                existing = await client.find_flag_by_key(
                    int(approval.action_payload["project_id"]),
                    str(approval.action_payload["key"]),
                )
                if existing is not None:
                    return {
                        "ok": False,
                        "details": {"error": f"Flag '{approval.action_payload['key']}' already exists."},
                    }
            except ValueError:
                pass
        return {"ok": True, "details": {"ready": True}}

    if norm == "flagsmith_upsert_percentage_segment":
        return {"ok": True, "details": {"ready": True}}

    if norm in (
        "flagsmith_toggle_flag",
        "flagsmith_set_gradual_rollout_stage",
        "flagsmith_configure_multivariate_split",
        "flagsmith_rollback_flag",
    ):
        return await _revalidate_flag_state(ctx, approval)

    if norm in (
        "flagsmith_delete_flag",
        "flagsmith_archive_flag",
        "flagsmith_delete_or_archive_flag",
    ):
        flag_reval = await _revalidate_flag_state(ctx, approval)
        if not flag_reval.get("ok"):
            return flag_reval
        flag_key = str(approval.action_payload.get("flag_key") or "")
        pid = await ctx.resolve_flagsmith_project_id()
        flag = await ctx.find_feature_flag(flag_key=flag_key, project_id=pid)
        if flag is not None:
            from app.agents.tools.flag_tools import check_flag_retirement_preconditions

            gh_client: Any | None = None
            try:
                gh_client = await ctx.get_github_client()
            except Exception:  # noqa: BLE001
                gh_client = None
            pre_ok, pre_details = await check_flag_retirement_preconditions(
                ctx.session, flag, github_client=gh_client
            )
            if not pre_ok:
                return {"ok": False, "details": pre_details}
        return flag_reval

    if norm in (
        "experiment_activate",
        "activate_experiment",
        "experiment_stop",
        "stop_or_conclude_experiment",
        "promote_experiment_winner",
        "experiment_promote_winner",
    ):
        exp = await ctx.session.get(
            Experiment, uuid.UUID(str(approval.action_payload["experiment_id"]))
        )
        if exp is None:
            return {"ok": False, "details": {"error": "Experiment not found."}}
        expected_status = pre.get("status")
        if expected_status and exp.status.value != expected_status:
            return {
                "ok": False,
                "details": {
                    "expected_status": expected_status,
                    "actual_status": exp.status.value,
                },
            }
        return {"ok": True, "details": {"status": exp.status.value}}

    if norm in ("progressive_rollout_activate_stage", "progressive_rollout_rollback"):
        from app.services.progressive_delivery.rollout_service import (
            TERMINAL_ROLLOUT_STATES,
        )

        rollout_raw_id = approval.action_payload.get("rollout_id") or approval.target_resource_id
        rollout = await ctx.session.get(ProgressiveRollout, uuid.UUID(str(rollout_raw_id)))
        if rollout is None:
            return {"ok": False, "details": {"error": "Progressive rollout not found."}}
        if rollout.status in TERMINAL_ROLLOUT_STATES:
            return {
                "ok": False,
                "details": {
                    "error": f"Progressive rollout is in terminal state '{rollout.status.value}'."
                },
            }
        expected_ver = pre.get("rollout_version")
        if expected_ver is not None and rollout.version != int(expected_ver):
            return {
                "ok": False,
                "details": {
                    "error": "Progressive rollout modified since approval was requested.",
                    "expected_version": expected_ver,
                    "actual_version": rollout.version,
                },
            }
        expected_pct = pre.get("current_percentage")
        if (
            norm == "progressive_rollout_activate_stage"
            and expected_pct is not None
            and rollout.current_percentage != int(expected_pct)
        ):
            return {
                "ok": False,
                "details": {
                    "error": "Rollout percentage changed since approval was requested.",
                    "expected_percentage": expected_pct,
                    "actual_percentage": rollout.current_percentage,
                },
            }
        flag = await ctx.session.get(FeatureFlag, rollout.feature_flag_id)
        if flag is None or flag.is_archived:
            return {"ok": False, "details": {"error": "Feature flag is missing or archived."}}
        if norm == "progressive_rollout_activate_stage":
            stage_num = approval.action_payload.get("stage_number")
            if stage_num is not None:
                stage = (
                    await ctx.session.execute(
                        select(ProgressiveRolloutStage)
                        .where(
                            ProgressiveRolloutStage.rollout_id == rollout.id,
                            ProgressiveRolloutStage.stage_number == int(stage_num),
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if stage is None or stage.status in {
                    ProgressiveStageStatus.PASSED,
                    ProgressiveStageStatus.SKIPPED,
                }:
                    return {
                        "ok": False,
                        "details": {
                            "error": f"Stage {stage_num} is missing or already completed."
                        },
                    }
        return {
            "ok": True,
            "details": {
                "rollout_version": rollout.version,
                "current_percentage": rollout.current_percentage,
                "status": rollout.status.value,
            },
        }

    if norm == "release_rollout_approve":
        from app.models import Release

        rel = await ctx.session.get(Release, uuid.UUID(str(approval.target_resource_id)))
        if rel is None:
            return {"ok": False, "details": {"error": "Release not found."}}
        expected_ver = pre.get("release_version")
        if expected_ver is not None and rel.version != int(expected_ver):
            return {
                "ok": False,
                "details": {
                    "error": "Release modified since approval was requested.",
                    "expected_version": expected_ver,
                    "actual_version": rel.version,
                },
            }
        return {"ok": True, "details": {"release_version": rel.version}}

    return {"ok": False, "details": {"error": f"Unknown action_type '{approval.action_type}'"}}


async def _resolve_rollout_flagsmith_client(
    ctx: ToolExecutionContext,
    flag: FeatureFlag | None,
) -> Any | None:
    if ctx.flagsmith_client is not None:
        return ctx.flagsmith_client
    try:
        return await ctx.get_flagsmith_client()
    except ValueError:
        if flag is not None and flag.flagsmith_feature_id is not None:
            raise
        return None


async def _run_execution(
    ctx: ToolExecutionContext,
    approval: Approval,
) -> dict[str, Any]:
    norm = _normalize_action(approval.action_type)
    payload = dict(approval.action_payload)

    if norm == "github_create_branch_and_draft_pr":
        args = GitHubCreateBranchAndDraftPRInput.model_validate(payload)
        return await handle_github_create_branch_and_draft_pr(ctx, args)

    if norm == "flagsmith_create_flag":
        args = FlagsmithCreateFlagInput.model_validate(payload)
        return await handle_flagsmith_create_flag(ctx, args)

    if norm == "flagsmith_upsert_percentage_segment":
        args = FlagsmithUpsertPercentageSegmentInput.model_validate(payload)
        return await handle_flagsmith_upsert_percentage_segment(ctx, args)

    if norm == "flagsmith_toggle_flag":
        args = FlagsmithToggleFlagInput.model_validate(payload)
        return await handle_flagsmith_toggle_flag(ctx, args, record_rollout=False)

    if norm == "flagsmith_set_gradual_rollout_stage":
        args = FlagsmithSetGradualRolloutStageInput.model_validate(payload)
        return await handle_flagsmith_set_gradual_rollout_stage(ctx, args, record_rollout=False)

    if norm == "flagsmith_configure_multivariate_split":
        args = FlagsmithConfigureMultivariateSplitInput.model_validate(payload)
        return await handle_flagsmith_configure_multivariate_split(ctx, args, record_rollout=False)

    if norm == "flagsmith_rollback_flag":
        args = FlagsmithRollbackFlagInput.model_validate(payload)
        return await handle_flagsmith_rollback_flag(ctx, args, record_rollout=False)

    if norm in ("flagsmith_delete_or_archive_flag", "flagsmith_delete_flag", "flagsmith_archive_flag"):
        if "action" not in payload:
            payload["action"] = "delete" if "delete" in norm else "archive"
        flag_key = str(payload.get("flag_key") or "")
        pid = await ctx.resolve_flagsmith_project_id()
        flag = await ctx.find_feature_flag(flag_key=flag_key, project_id=pid)
        if flag is not None:
            from app.models.enums import FlagLifecycleStatus

            flag.lifecycle_status = FlagLifecycleStatus.CLEANUP_APPROVED
            await ctx.session.flush()
        args = FlagsmithDeleteOrArchiveFlagInput.model_validate(payload)
        return await handle_flagsmith_delete_or_archive_flag(ctx, args)

    if norm in ("experiment_activate", "activate_experiment"):
        args = ActivateExperimentInput.model_validate(payload)
        return await handle_activate_experiment(ctx, args)

    if norm in ("experiment_stop", "stop_or_conclude_experiment"):
        args = StopOrConcludeExperimentInput.model_validate(payload)
        return await handle_stop_or_conclude_experiment(ctx, args)

    if norm in ("promote_experiment_winner", "experiment_promote_winner"):
        return await handle_promote_experiment_winner(
            ctx,
            payload["experiment_id"],
            payload.get("winning_variation"),
        )

    if norm == "release_rollout_approve":
        return {
            "verified": True,
            "release_id": str(approval.target_resource_id),
            "release_version": payload.get("release_version"),
        }

    if norm == "progressive_rollout_activate_stage":
        from app.services.progressive_delivery.rollout_service import (
            ProgressiveDeliveryService,
        )

        rollout_raw_id = payload.get("rollout_id") or approval.target_resource_id
        rollout = await ctx.session.get(ProgressiveRollout, uuid.UUID(str(rollout_raw_id)))
        if rollout is None:
            raise ValueError(f"ProgressiveRollout {rollout_raw_id} not found.")
        stage: ProgressiveRolloutStage | None = None
        if payload.get("stage_number") is not None:
            stage = (
                await ctx.session.execute(
                    select(ProgressiveRolloutStage)
                    .where(
                        ProgressiveRolloutStage.rollout_id == rollout.id,
                        ProgressiveRolloutStage.stage_number == int(payload["stage_number"]),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
        if stage is None and rollout.current_stage_id is not None:
            stage = await ctx.session.get(ProgressiveRolloutStage, rollout.current_stage_id)
        if stage is None:
            stage = (
                await ctx.session.execute(
                    select(ProgressiveRolloutStage)
                    .where(
                        ProgressiveRolloutStage.rollout_id == rollout.id,
                        ProgressiveRolloutStage.status.in_(
                            [
                                ProgressiveStageStatus.AWAITING_APPROVAL,
                                ProgressiveStageStatus.PENDING,
                            ]
                        ),
                    )
                    .order_by(ProgressiveRolloutStage.stage_number)
                    .limit(1)
                )
            ).scalar_one_or_none()
        if stage is None:
            raise ValueError(f"No pending stage found for rollout {rollout.id}.")

        flag = await ctx.session.get(FeatureFlag, rollout.feature_flag_id)
        fs_client = await _resolve_rollout_flagsmith_client(ctx, flag)
        actor_id = ctx.user_id or approval.decided_by_id or approval.requested_by_id
        await ProgressiveDeliveryService._activate_stage(
            ctx.session,
            rollout,
            stage,
            actor_id=actor_id,
            flagsmith_client=fs_client,
        )
        return {
            "verified": True,
            "rollout_id": str(rollout.id),
            "rollout_key": rollout.rollout_key,
            "stage_number": stage.stage_number,
            "target_percentage": stage.target_percentage,
            "status": rollout.status.value,
        }

    if norm == "progressive_rollout_rollback":
        from app.services.progressive_delivery.rollout_service import (
            ProgressiveDeliveryService,
        )

        rollout_raw_id = payload.get("rollout_id") or approval.target_resource_id
        rollout = await ctx.session.get(ProgressiveRollout, uuid.UUID(str(rollout_raw_id)))
        if rollout is None:
            raise ValueError(f"ProgressiveRollout {rollout_raw_id} not found.")
        flag = await ctx.session.get(FeatureFlag, rollout.feature_flag_id)
        fs_client = await _resolve_rollout_flagsmith_client(ctx, flag)
        actor_id = ctx.user_id or approval.decided_by_id or approval.requested_by_id
        op = await ProgressiveDeliveryService.execute_rollback(
            ctx.session,
            rollout,
            actor_id=actor_id,
            reason=str(
                payload.get("reason")
                or approval.rationale
                or "Approved progressive rollout rollback"
            ),
            flagsmith_client=fs_client,
            has_production_approval=True,
        )
        op.approval_id = approval.id
        await ctx.session.flush()
        return {
            "verified": True,
            "rollback_operation_id": str(op.id),
            "rollout_id": str(rollout.id),
            "status": rollout.status.value,
            "current_percentage": rollout.current_percentage,
        }

    raise ValueError(f"Unsupported approval action_type '{approval.action_type}'.")


async def _run_post_verification(
    ctx: ToolExecutionContext,
    approval: Approval,
    exec_result: dict[str, Any],
) -> dict[str, Any]:
    del ctx, approval
    if "verified" in exec_result:
        return {
            "matches": bool(exec_result["verified"]),
            "exec_summary": exec_result,
        }
    return {"matches": True, "exec_summary": exec_result}


def _rollout_action_for(action_type: str, payload: dict[str, Any]) -> RolloutAction:
    norm = _normalize_action(action_type)
    if norm == "flagsmith_toggle_flag":
        return RolloutAction.ENABLE if payload.get("enabled") else RolloutAction.DISABLE
    if norm == "flagsmith_rollback_flag":
        return RolloutAction.ROLLBACK
    if norm == "flagsmith_configure_multivariate_split":
        return RolloutAction.MULTIVARIATE_UPDATE
    if norm == "flagsmith_set_gradual_rollout_stage":
        return (
            RolloutAction.ROLLBACK
            if int(payload.get("target_percentage", 0)) == 0
            else RolloutAction.ROLLOUT_STEP
        )
    return RolloutAction.ROLLOUT_STEP


async def execute_approval_request(
    ctx: ToolExecutionContext,
    approval: Approval,
    *,
    decided_by_id: uuid.UUID | None,
    comment: str | None = None,
) -> Approval:
    """Run PolicyEngine single-use atomic lock, drift check, execution, and verification."""
    norm = _normalize_action(approval.action_type)
    rollout_ctx: dict[str, Any] | None = None

    if norm in (
        "flagsmith_toggle_flag",
        "flagsmith_set_gradual_rollout_stage",
        "flagsmith_configure_multivariate_split",
        "flagsmith_rollback_flag",
    ):
        flag: FeatureFlag | None = None
        try:
            flag = await ctx.session.get(FeatureFlag, uuid.UUID(approval.target_resource_id))
        except ValueError:
            flag = await ctx.find_feature_flag(
                flag_key=str(approval.action_payload.get("flag_key", ""))
            )
        if flag is not None:
            rollout_ctx = {
                "feature_flag_id": flag.id,
                "action": _rollout_action_for(norm, approval.action_payload),
                "previous_state": dict(approval.expected_precondition or {}),
                "new_state": dict(approval.action_payload or {}),
            }

    async def revalidator(app: Approval) -> dict[str, Any]:
        return await _run_revalidation(ctx, app)

    async def executor(app: Approval) -> dict[str, Any]:
        return await _run_execution(ctx, app)

    async def post_verifier(app: Approval, res: dict[str, Any]) -> dict[str, Any]:
        return await _run_post_verification(ctx, app, res)

    return await PolicyEngine.execute_approved_action(
        ctx.session,
        approval.id,
        decided_by_id=decided_by_id,
        comment=comment,
        revalidator=revalidator,
        executor=executor,
        post_verifier=post_verifier,
        rollout_recorder_context=rollout_ctx,
    )
