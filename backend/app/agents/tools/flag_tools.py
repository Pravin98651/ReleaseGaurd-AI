"""13 flag_agent manual tools for Flagsmith management, rollouts, verification, and health scanning."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools.context import ToolExecutionContext
from app.analytics.flag_health import (
    FlagDependencyReport,
    FlagHealthInput,
    evaluate_flag_cleanup_readiness,
    scan_files_for_flag_references,
    scan_project_flag_health,
)
from app.core.database import utcnow
from app.core.security import make_token_hint
from app.models import (
    CodeChange,
    CodeChangeType,
    Experiment,
    ExperimentEvent,
    ExperimentEventType,
    ExperimentStatus,
    FeatureFlag,
    FlagLifecycleStatus,
    FlagType,
    ProgressiveRollout,
    ProgressiveRolloutStatus,
    PullRequest,
    PullRequestState,
    Repository,
    RolloutAction,
)
from app.schemas.tools import (
    FlagsmithConfigureMultivariateSplitInput,
    FlagsmithConfigureMultivariateSplitOutput,
    FlagsmithCreateFlagInput,
    FlagsmithCreateFlagOutput,
    FlagsmithDeleteOrArchiveFlagInput,
    FlagsmithExplainIdentityInput,
    FlagsmithExplainIdentityOutput,
    PlanFeatureRequestRolloutInput,
    PlanFeatureRequestRolloutOutput,
    FlagsmithDeleteOrArchiveFlagOutput,
    FlagsmithGetFlagStateInput,
    FlagsmithGetFlagStateOutput,
    FlagsmithListEnvironmentsInput,
    FlagsmithListEnvironmentsOutput,
    FlagsmithListFlagsInput,
    FlagsmithListFlagsOutput,
    FlagsmithListProjectsInput,
    FlagsmithListProjectsOutput,
    FlagsmithRollbackFlagInput,
    FlagsmithRollbackFlagOutput,
    FlagsmithSetGradualRolloutStageInput,
    FlagsmithSetGradualRolloutStageOutput,
    FlagsmithToggleFlagInput,
    FlagsmithToggleFlagOutput,
    FlagsmithUpsertPercentageSegmentInput,
    FlagsmithUpsertPercentageSegmentOutput,
    FlagsmithVerifyLiveStateInput,
    FlagsmithVerifyLiveStateOutput,
    ScanFlagHealthInput,
    ScanFlagHealthOutput,
)
from app.services.policy_engine import PolicyEngine, is_production_environment


async def handle_flagsmith_list_projects(
    ctx: ToolExecutionContext,
    args: FlagsmithListProjectsInput,
) -> dict[str, Any]:
    del args
    client = await ctx.get_flagsmith_client()
    raw = await client.list_projects()
    projects = [
        {
            "id": p.get("id"),
            "name": p.get("name", ""),
            "organisation_id": p.get("organisation"),
        }
        for p in raw
    ]
    return FlagsmithListProjectsOutput(projects=projects).model_dump(mode="json")


async def handle_flagsmith_list_environments(
    ctx: ToolExecutionContext,
    args: FlagsmithListEnvironmentsInput,
) -> dict[str, Any]:
    client = await ctx.get_flagsmith_client()
    raw = await client.list_environments(args.project_id)
    envs = [
        {
            "id": e.get("id"),
            "name": e.get("name", ""),
            "api_key": make_token_hint(str(e.get("api_key", ""))),
            "is_production": bool(
                e["is_production"]
                if "is_production" in e
                else is_production_environment(str(e.get("name", "")))
            ),
            "use_v2_feature_versioning": bool(e.get("use_v2_feature_versioning", False)),
        }
        for e in raw
    ]
    return FlagsmithListEnvironmentsOutput(environments=envs).model_dump(mode="json")


async def handle_flagsmith_list_flags(
    ctx: ToolExecutionContext,
    args: FlagsmithListFlagsInput,
) -> dict[str, Any]:
    client = await ctx.get_flagsmith_client()
    raw = await client.list_flags(args.project_id)
    flags_out: list[dict[str, Any]] = []
    for f in raw:
        key = str(f.get("name", ""))
        synced = await ctx.sync_flag_from_flagsmith(key, project_id=args.project_id)
        item: dict[str, Any] = {
            "id": str(synced.id),
            "flagsmith_feature_id": synced.flagsmith_feature_id,
            "key": synced.key,
            "flag_type": synced.flag_type.value,
            "is_archived": synced.is_archived,
            "env_states": synced.env_states,
        }
        if args.environment_name:
            item["selected_env_state"] = (synced.env_states or {}).get(
                args.environment_name.strip().lower()
            )
        flags_out.append(item)
    await ctx.session.commit()
    return FlagsmithListFlagsOutput(flags=flags_out).model_dump(mode="json")


async def handle_flagsmith_get_flag_state(
    ctx: ToolExecutionContext,
    args: FlagsmithGetFlagStateInput,
) -> dict[str, Any]:
    client = await ctx.get_flagsmith_client()
    pid = await ctx.resolve_flagsmith_project_id()
    flag = await ctx.find_feature_flag(flag_key=args.flag_key, project_id=pid)
    if flag is None and pid is not None:
        flag = await ctx.sync_flag_from_flagsmith(args.flag_key, project_id=pid)
        await ctx.session.commit()

    feature_id = flag.flagsmith_feature_id if flag else None
    if feature_id is None and pid is not None:
        remote = await client.find_flag_by_key(pid, args.flag_key)
        if remote is not None:
            feature_id = int(remote["id"])
    if feature_id is None:
        raise ValueError(f"Feature flag '{args.flag_key}' not found in Flagsmith.")

    env_key = await ctx.resolve_env_key(args.environment_name, flag=flag, project_id=pid)
    states = await client.get_environment_feature_states(env_key, feature_id=feature_id)
    base_fs, _ = client._partition_feature_states(states)

    edge_state: dict[str, Any] | None = None
    if args.identity_id:
        edge_state = await client.get_edge_identity_flags(env_key, args.identity_id)

    return FlagsmithGetFlagStateOutput(
        flag_key=args.flag_key,
        environment_name=args.environment_name,
        management_state={
            "base": base_fs or {},
            "states": states,
        },
        edge_state=edge_state,
    ).model_dump(mode="json")


async def handle_flagsmith_create_flag(
    ctx: ToolExecutionContext,
    args: FlagsmithCreateFlagInput,
) -> dict[str, Any]:
    client = await ctx.get_flagsmith_client()
    mv_dicts = [m.model_dump(mode="json") for m in args.mv_options] if args.mv_options else None
    created = await client.create_flag(
        project_id=args.project_id,
        key=args.key,
        description=args.description,
        flag_type=args.flag_type.value,
        initial_value=args.initial_value,
        default_enabled=False,
        mv_options=mv_dicts,
    )
    await ctx.sync_flag_from_flagsmith(args.key, project_id=args.project_id)
    await ctx.session.commit()
    return FlagsmithCreateFlagOutput(
        feature_id=int(created["id"]),
        key=str(created.get("name") or args.key),
        flag_type=FlagType(str(created.get("type") or args.flag_type.value).upper()),
    ).model_dump(mode="json")


async def handle_flagsmith_upsert_percentage_segment(
    ctx: ToolExecutionContext,
    args: FlagsmithUpsertPercentageSegmentInput,
) -> dict[str, Any]:
    client = await ctx.get_flagsmith_client()
    seg = await client.upsert_percentage_split_segment(
        project_id=args.project_id,
        segment_name=args.segment_name,
        percentage=args.percentage,
    )
    return FlagsmithUpsertPercentageSegmentOutput(
        segment_id=int(seg["id"]),
        segment_name=str(seg.get("name") or args.segment_name),
        percentage=args.percentage,
    ).model_dump(mode="json")


async def _resolve_flag_and_env(
    ctx: ToolExecutionContext,
    flag_key: str,
    environment_name: str,
) -> tuple[FeatureFlag, str, int, int]:
    pid = await ctx.resolve_flagsmith_project_id()
    flag = await ctx.find_feature_flag(flag_key=flag_key, project_id=pid)
    if flag is None and pid is not None:
        flag = await ctx.sync_flag_from_flagsmith(flag_key, project_id=pid)
    if flag is None:
        raise ValueError(f"Feature flag '{flag_key}' not found.")
    if flag.flagsmith_feature_id is None:
        flag = await ctx.sync_flag_from_flagsmith(flag_key, project_id=flag.flagsmith_project_id)
    assert flag.flagsmith_feature_id is not None
    env_key = await ctx.resolve_env_key(
        environment_name, flag=flag, project_id=flag.flagsmith_project_id
    )
    return flag, env_key, flag.flagsmith_project_id, int(flag.flagsmith_feature_id)


async def handle_flagsmith_toggle_flag(
    ctx: ToolExecutionContext,
    args: FlagsmithToggleFlagInput,
    *,
    record_rollout: bool = True,
) -> dict[str, Any]:
    client = await ctx.get_flagsmith_client()
    flag, env_key, pid, feature_id = await _resolve_flag_and_env(
        ctx, args.flag_key, args.environment_name
    )
    env_norm = args.environment_name.strip().lower()
    prev_state = dict((flag.env_states or {}).get(env_norm) or {})

    res = await client.toggle_flag(
        env_key=env_key,
        feature_id=feature_id,
        enabled=args.enabled,
        flag_key=args.flag_key,
        verify_edge=ctx.verify_edge_on_mutation,
    )
    verification = res.get("verification") or {}
    verified = bool(verification.get("matches", False))
    actual_state = dict(verification.get("actual_state") or {})

    await ctx.sync_flag_from_flagsmith(args.flag_key, project_id=pid)
    if record_rollout:
        await PolicyEngine.record_rollout_history(
            ctx.session,
            feature_flag_id=flag.id,
            environment_name=env_norm,
            action=RolloutAction.ENABLE if args.enabled else RolloutAction.DISABLE,
            previous_state=prev_state,
            new_state={
                "enabled": args.enabled,
                "rollout_percentage": 100 if args.enabled else 0,
                **actual_state,
            },
            verified=verified,
            verification_details=verification,
            actor_id=ctx.user_id,
            reason=args.reason,
        )
    await ctx.session.commit()
    return FlagsmithToggleFlagOutput(
        flag_key=args.flag_key,
        environment_name=args.environment_name,
        enabled=args.enabled,
        verified=verified,
    ).model_dump(mode="json")


async def handle_flagsmith_set_gradual_rollout_stage(
    ctx: ToolExecutionContext,
    args: FlagsmithSetGradualRolloutStageInput,
    *,
    record_rollout: bool = True,
) -> dict[str, Any]:
    client = await ctx.get_flagsmith_client()
    flag, env_key, pid, feature_id = await _resolve_flag_and_env(
        ctx, args.flag_key, args.environment_name
    )
    env_norm = args.environment_name.strip().lower()
    prev_state = dict((flag.env_states or {}).get(env_norm) or {})

    res = await client.set_gradual_rollout_stage(
        project_id=pid,
        env_key=env_key,
        feature_id=feature_id,
        flag_key=args.flag_key,
        target_percentage=args.target_percentage,
        strategy=args.strategy,
        verify_edge=ctx.verify_edge_on_mutation,
    )
    verification = res.get("verification") or {}
    verified = bool(verification.get("matches", False))

    await ctx.sync_flag_from_flagsmith(args.flag_key, project_id=pid)
    if record_rollout:
        await PolicyEngine.record_rollout_history(
            ctx.session,
            feature_flag_id=flag.id,
            environment_name=env_norm,
            action=(
                RolloutAction.ROLLBACK
                if args.target_percentage == 0
                else RolloutAction.ROLLOUT_STEP
            ),
            previous_state=prev_state,
            new_state={
                "target_percentage": args.target_percentage,
                "strategy": args.strategy,
                **(verification.get("actual_state") or {}),
            },
            verified=verified,
            verification_details=verification,
            actor_id=ctx.user_id,
            reason=args.reason,
        )
    await ctx.session.commit()
    return FlagsmithSetGradualRolloutStageOutput(
        flag_key=args.flag_key,
        environment_name=args.environment_name,
        target_percentage=args.target_percentage,
        strategy=args.strategy,
        verified=verified,
    ).model_dump(mode="json")


async def handle_flagsmith_configure_multivariate_split(
    ctx: ToolExecutionContext,
    args: FlagsmithConfigureMultivariateSplitInput,
    *,
    record_rollout: bool = True,
) -> dict[str, Any]:
    client = await ctx.get_flagsmith_client()
    flag, env_key, pid, feature_id = await _resolve_flag_and_env(
        ctx, args.flag_key, args.environment_name
    )
    env_norm = args.environment_name.strip().lower()
    prev_state = dict((flag.env_states or {}).get(env_norm) or {})

    res = await client.configure_multivariate_split(
        project_id=pid,
        env_key=env_key,
        feature_id=feature_id,
        flag_key=args.flag_key,
        allocations=args.allocations,
        verify_edge=ctx.verify_edge_on_mutation,
    )
    verification = res.get("verification") or {}
    verified = bool(verification.get("matches", False))

    await ctx.sync_flag_from_flagsmith(args.flag_key, project_id=pid)
    if record_rollout:
        await PolicyEngine.record_rollout_history(
            ctx.session,
            feature_flag_id=flag.id,
            environment_name=env_norm,
            action=RolloutAction.MULTIVARIATE_UPDATE,
            previous_state=prev_state,
            new_state={
                "allocations": args.allocations,
                **(verification.get("actual_state") or {}),
            },
            verified=verified,
            verification_details=verification,
            actor_id=ctx.user_id,
            reason=args.reason,
        )
    await ctx.session.commit()
    return FlagsmithConfigureMultivariateSplitOutput(
        flag_key=args.flag_key,
        environment_name=args.environment_name,
        allocations=args.allocations,
        verified=verified,
    ).model_dump(mode="json")


async def handle_flagsmith_rollback_flag(
    ctx: ToolExecutionContext,
    args: FlagsmithRollbackFlagInput,
    *,
    record_rollout: bool = True,
) -> dict[str, Any]:
    client = await ctx.get_flagsmith_client()
    flag, env_key, pid, feature_id = await _resolve_flag_and_env(
        ctx, args.flag_key, args.environment_name
    )
    env_norm = args.environment_name.strip().lower()
    prev_state = dict((flag.env_states or {}).get(env_norm) or {})

    res = await client.rollback_flag(
        project_id=pid,
        env_key=env_key,
        feature_id=feature_id,
        flag_key=args.flag_key,
        verify_edge=ctx.verify_edge_on_mutation,
    )
    verification = res.get("verification") or {}
    verified = bool(verification.get("matches", False))

    await ctx.sync_flag_from_flagsmith(args.flag_key, project_id=pid)
    if record_rollout:
        await PolicyEngine.record_rollout_history(
            ctx.session,
            feature_flag_id=flag.id,
            environment_name=env_norm,
            action=RolloutAction.ROLLBACK,
            previous_state=prev_state,
            new_state={
                "enabled": False,
                "rollout_percentage": 0,
                **(verification.get("actual_state") or {}),
            },
            verified=verified,
            verification_details=verification,
            actor_id=ctx.user_id,
            reason=args.reason,
        )
    await ctx.session.commit()
    return FlagsmithRollbackFlagOutput(
        flag_key=args.flag_key,
        environment_name=args.environment_name,
        rolled_back=True,
        verified=verified,
    ).model_dump(mode="json")


async def handle_flagsmith_verify_live_state(
    ctx: ToolExecutionContext,
    args: FlagsmithVerifyLiveStateInput,
) -> dict[str, Any]:
    client = await ctx.get_flagsmith_client()
    flag, env_key, pid, feature_id = await _resolve_flag_and_env(
        ctx, args.flag_key, args.environment_name
    )
    del flag
    verification = await client.verify_live_state(
        env_key=env_key,
        feature_id=feature_id,
        flag_key=args.flag_key,
        project_id=pid,
        expected_enabled=args.expected_enabled,
        expected_percentage=args.expected_percentage,
        verify_edge=ctx.verify_edge_on_mutation,
    )
    return FlagsmithVerifyLiveStateOutput(
        matches=bool(verification.get("matches", False)),
        actual_state=dict(verification.get("actual_state") or {}),
        mismatches=list(verification.get("mismatches") or []),
    ).model_dump(mode="json")


async def handle_scan_flag_health(
    ctx: ToolExecutionContext,
    args: ScanFlagHealthInput,
) -> dict[str, Any]:
    ws = await PolicyEngine.get_workspace_settings(ctx.session)
    thresholds = dict(ws.health_thresholds or {})
    unused_days = int(thresholds.get("unused_days", 14))
    cutoff = utcnow() - timedelta(days=unused_days)

    stmt = select(FeatureFlag)
    if args.project_id is not None:
        stmt = stmt.where(FeatureFlag.flagsmith_project_id == args.project_id)
    flags = list((await ctx.session.execute(stmt)).scalars().all())

    # Count recent exposure events per flag_key
    ev_stmt = (
        select(ExperimentEvent.flag_key, func.count(ExperimentEvent.id))
        .where(
            ExperimentEvent.event_type == ExperimentEventType.FEATURE_VARIATION_ASSIGNED,
            ExperimentEvent.occurred_at >= cutoff,
        )
        .group_by(ExperimentEvent.flag_key)
    )
    assignment_counts = {
        str(fk): int(cnt) for fk, cnt in (await ctx.session.execute(ev_stmt)).all()
    }

    # Check active experiments per feature_flag_id
    exp_stmt = select(Experiment.feature_flag_id).where(
        Experiment.status == ExperimentStatus.RUNNING
    )
    active_exp_flag_ids = {row[0] for row in (await ctx.session.execute(exp_stmt)).all()}

    scanned_refs_by_flag: dict[str, list[dict[str, Any]]] | None = None
    repo: Repository | None = None
    if args.repository_id is not None:
        repo = await ctx.session.get(Repository, args.repository_id)
    if ctx.repo_base_files is not None:
        refs = scan_files_for_flag_references(ctx.repo_base_files)
        scanned_refs_by_flag = {
            k: [r.model_dump(mode="json") for r in v] for k, v in refs.items()
        }
    elif repo is not None:
        try:
            client = await ctx.get_github_client()
            tree = await client.get_repo_tree(
                repo.owner, repo.name, tree_sha=repo.default_branch, recursive=True
            )
            ignored_prefixes = (
                "node_modules/",
                ".venv/",
                "venv/",
                "dist/",
                "build/",
                ".next/",
                "__pycache__/",
                ".git/",
            )
            candidate_paths = [
                str(entry.get("path", ""))
                for entry in tree
                if entry.get("type") == "blob"
                and str(entry.get("path", "")).lower().endswith(
                    (".py", ".ts", ".tsx", ".js", ".jsx")
                )
                and not any(
                    str(entry.get("path", "")).startswith(prefix)
                    or f"/{prefix}" in str(entry.get("path", ""))
                    for prefix in ignored_prefixes
                )
            ]
            sem = asyncio.Semaphore(10)

            async def _fetch_blob(path: str) -> tuple[str, str | None]:
                async with sem:
                    try:
                        info = await client.get_file_contents(
                            repo.owner, repo.name, path, ref=repo.default_branch
                        )
                        return path, str(info.get("content", ""))
                    except Exception:  # noqa: BLE001
                        return path, None

            fetched = await asyncio.gather(*(_fetch_blob(p) for p in candidate_paths))
            file_map: dict[str, str] = {
                path: content for path, content in fetched if content is not None
            }
            refs = scan_files_for_flag_references(file_map)
            scanned_refs_by_flag = {
                k: [r.model_dump(mode="json") for r in v] for k, v in refs.items()
            }
        except Exception:  # noqa: BLE001
            scanned_refs_by_flag = None

    health_inputs: list[FlagHealthInput] = []
    for f in flags:
        refs_for_flag = (
            scanned_refs_by_flag.get(f.key, [])
            if scanned_refs_by_flag is not None
            else list(f.code_references or [])
        )
        env_states = dict(f.env_states or {})
        has_partial = any(
            0 < int(st.get("rollout_percentage") or 0) < 100
            for st in env_states.values()
            if isinstance(st, dict)
        )
        health_inputs.append(
            FlagHealthInput(
                key=f.key,
                flagsmith_project_id=f.flagsmith_project_id,
                description=f.description,
                flag_type=f.flag_type.value,
                is_archived=f.is_archived,
                env_states=env_states,
                mv_options=list(f.mv_options or []),
                code_references=refs_for_flag,
                recent_assignment_count=assignment_counts.get(f.key, 0),
                has_active_experiment=f.id in active_exp_flag_ids,
                has_active_rollout=has_partial,
                repo_scanned=scanned_refs_by_flag is not None or bool(f.code_references),
                flagsmith_created_at=f.flagsmith_created_at,
                updated_at=f.updated_at,
                created_at=f.created_at,
            )
        )

    report = scan_project_flag_health(
        health_inputs,
        code_references_by_flag=scanned_refs_by_flag,
        thresholds=thresholds,
    )

    result_by_key = {r.flag_key: r for r in report.flags}
    now = utcnow()
    serialized_results: list[dict[str, Any]] = []
    for f in flags:
        res = result_by_key.get(f.key)
        if res is not None:
            f.health_score = res.health_score
            f.health_issues = [iss.model_dump(mode="json") for iss in res.health_issues]
            if scanned_refs_by_flag is not None:
                f.code_references = scanned_refs_by_flag.get(f.key, [])
            f.last_health_scan_at = now
            serialized_results.append(
                {
                    "flag_id": str(f.id),
                    "key": f.key,
                    "flag_type": f.flag_type.value,
                    "health_score": f.health_score,
                    "health_issues": f.health_issues,
                    "code_references_count": len(f.code_references or []),
                    "last_health_scan_at": now.isoformat(),
                }
            )

    await ctx.session.commit()
    return ScanFlagHealthOutput(
        flags_scanned=report.total_flags,
        unhealthy_flags=report.unhealthy_flags,
        results=serialized_results,
    ).model_dump(mode="json")


async def handle_flagsmith_delete_or_archive_flag(
    ctx: ToolExecutionContext,
    args: FlagsmithDeleteOrArchiveFlagInput,
) -> dict[str, Any]:
    client = await ctx.get_flagsmith_client()
    pid = await ctx.resolve_flagsmith_project_id()
    flag = await ctx.find_feature_flag(flag_key=args.flag_key, project_id=pid)
    if flag is None and pid is not None:
        flag = await ctx.sync_flag_from_flagsmith(args.flag_key, project_id=pid)
    if flag is None or flag.flagsmith_feature_id is None:
        raise ValueError(f"Feature flag '{args.flag_key}' not found in Flagsmith.")

    if args.action == "delete":
        await client.delete_flag(flag.flagsmith_project_id, int(flag.flagsmith_feature_id))
        flag.is_archived = True
    else:
        await client.archive_flag(flag.flagsmith_project_id, int(flag.flagsmith_feature_id))
        flag.is_archived = True

    flag.lifecycle_status = FlagLifecycleStatus.CLEANED_UP
    details = dict(flag.lifecycle_details or {})
    details.update(
        {
            "retirement_action": args.action,
            "retirement_reason": args.reason,
            "cleaned_up_at": utcnow().isoformat(),
        }
    )
    flag.lifecycle_details = details

    await ctx.session.commit()
    return FlagsmithDeleteOrArchiveFlagOutput(
        flag_key=args.flag_key,
        action=args.action,
        completed=True,
    ).model_dump(mode="json")


async def transition_flag_post_promotion(
    session: AsyncSession,
    flag: FeatureFlag,
    *,
    source: str,
    environment_name: str | None = None,
    winning_variation: str | None = None,
    exclude_experiment_id: uuid.UUID | None = None,
    exclude_rollout_id: uuid.UUID | None = None,
) -> FlagDependencyReport:
    """Inspect flag dependencies after promotion/100% rollout and transition lifecycle state."""
    exp_stmt = select(Experiment).where(
        Experiment.feature_flag_id == flag.id,
        Experiment.status.in_([ExperimentStatus.RUNNING, ExperimentStatus.PAUSED]),
    )
    if exclude_experiment_id is not None:
        exp_stmt = exp_stmt.where(Experiment.id != exclude_experiment_id)
    active_exps = [
        e.name for e in (await session.execute(exp_stmt)).scalars().all()
    ]

    terminal_rollouts = {
        ProgressiveRolloutStatus.COMPLETED,
        ProgressiveRolloutStatus.ROLLED_BACK,
        ProgressiveRolloutStatus.CANCELLED,
    }
    rollout_stmt = select(ProgressiveRollout).where(
        ProgressiveRollout.feature_flag_key == flag.key,
    )
    if exclude_rollout_id is not None:
        rollout_stmt = rollout_stmt.where(ProgressiveRollout.id != exclude_rollout_id)
    active_rollouts = [
        f"{r.rollout_key} ({r.environment})"
        for r in (await session.execute(rollout_stmt)).scalars().all()
        if r.status not in terminal_rollouts
    ]

    partial_envs: list[str] = []
    target_env_norm = environment_name.strip().lower() if environment_name else None
    for env_name, st in (flag.env_states or {}).items():
        if not isinstance(st, dict):
            continue
        if target_env_norm and env_name.strip().lower() == target_env_norm:
            continue
        pct = st.get("rollout_percentage")
        if pct is not None and 0 < int(pct) < 100:
            partial_envs.append(env_name)

    report = evaluate_flag_cleanup_readiness(
        active_experiments=active_exps,
        active_rollouts=active_rollouts,
        partial_environments=partial_envs,
    )

    terminal_cleanup_states = {
        FlagLifecycleStatus.CLEANUP_PR_CREATED,
        FlagLifecycleStatus.CLEANUP_APPROVED,
        FlagLifecycleStatus.CLEANED_UP,
    }
    if flag.lifecycle_status not in terminal_cleanup_states:
        flag.lifecycle_status = (
            FlagLifecycleStatus.CLEANUP_RECOMMENDED
            if report.safe_for_cleanup
            else FlagLifecycleStatus.PROMOTED
        )

    details = dict(flag.lifecycle_details or {})
    details.update(
        {
            "cleanup_ready": report.safe_for_cleanup,
            "blockers": report.blockers,
            "active_experiments": report.active_experiments,
            "active_rollouts": report.active_rollouts,
            "partial_environments": report.partial_environments,
            "promotion_source": source,
            "checked_at": utcnow().isoformat(),
        }
    )
    if environment_name:
        details["environment_name"] = environment_name.strip().lower()
    if winning_variation:
        details["winning_variation"] = winning_variation
    flag.lifecycle_details = details
    return report


async def check_flag_retirement_preconditions(
    session: AsyncSession,
    flag: FeatureFlag,
    *,
    github_client: Any | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Verify cleanup PR is merged OR 0 code references remain before allowing retirement."""
    remaining_refs = len(flag.code_references or [])
    if remaining_refs == 0:
        return True, {
            "reason": "zero_code_references",
            "remaining_code_references": 0,
            "merged_cleanup_pr": False,
        }

    cleanup_changes = list(
        (
            await session.execute(
                select(CodeChange).where(
                    CodeChange.feature_flag_id == flag.id,
                    CodeChange.change_type == CodeChangeType.FLAG_CLEANUP,
                    CodeChange.draft_pr_number.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )

    open_prs: list[int] = []
    for change in cleanup_changes:
        if change.draft_pr_number is None:
            continue
        open_prs.append(int(change.draft_pr_number))
        if (change.validation_report or {}).get("cleanup_pr_merged") is True:
            return True, {
                "reason": "cleanup_pr_merged",
                "merged_pr_number": int(change.draft_pr_number),
                "remaining_code_references": remaining_refs,
            }

        pr_row = (
            await session.execute(
                select(PullRequest)
                .where(
                    PullRequest.repository_id == change.repository_id,
                    PullRequest.github_pr_number == int(change.draft_pr_number),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if pr_row is not None and pr_row.state == PullRequestState.MERGED:
            return True, {
                "reason": "cleanup_pr_merged",
                "merged_pr_number": int(change.draft_pr_number),
                "remaining_code_references": remaining_refs,
            }

        if github_client is not None and getattr(github_client, "token", ""):
            repo = await session.get(Repository, change.repository_id)
            if repo is not None:
                try:
                    live_pr = await github_client.get_pull_request(
                        repo.owner, repo.name, int(change.draft_pr_number)
                    )
                    if bool(live_pr.get("merged")):
                        return True, {
                            "reason": "cleanup_pr_merged",
                            "merged_pr_number": int(change.draft_pr_number),
                            "remaining_code_references": remaining_refs,
                        }
                except Exception:  # noqa: BLE001
                    return False, {
                        "reason": "unmerged_cleanup_pr_or_remaining_references",
                        "remaining_code_references": remaining_refs,
                        "open_cleanup_prs": open_prs,
                    }

    return False, {
        "reason": "unmerged_cleanup_pr_or_remaining_references",
        "remaining_code_references": remaining_refs,
        "open_cleanup_prs": open_prs,
    }


async def handle_flagsmith_explain_identity(
    ctx: ToolExecutionContext,
    args: FlagsmithExplainIdentityInput,
) -> dict[str, Any]:
    """Category A: Evaluate an identity against Flagsmith Edge and explain assigned flags."""
    from app.services.flagsmith_service import FlagsmithGovernanceService

    res = await FlagsmithGovernanceService.explain_identity(
        ctx,
        environment_name=args.environment_name,
        identifier=args.identifier,
        traits=args.traits,
    )
    return FlagsmithExplainIdentityOutput(
        identifier=res["identifier"],
        environment_name=res["environment_name"],
        consistent_on_repeat_evaluation=bool(res["consistent_on_repeat_evaluation"]),
        flag_explanations=list(res.get("flag_explanations") or []),
        markdown_summary=str(res.get("markdown_summary") or ""),
        evidence_note=str(res.get("evidence_note") or ""),
    ).model_dump(mode="json")


async def handle_plan_feature_request_rollout(
    ctx: ToolExecutionContext,
    args: PlanFeatureRequestRolloutInput,
) -> dict[str, Any]:
    """Category A: Turn a natural-language feature request into a flagged rollout plan."""
    from app.services.feature_request_planner import plan_feature_request_from_prompt

    res = await plan_feature_request_from_prompt(
        ctx.session,
        prompt=args.prompt,
        user_id=ctx.user_id,
        repository_id=args.repository_id,
        environment_name=args.environment_name,
        create_release=args.create_release,
    )
    return PlanFeatureRequestRolloutOutput(
        recommended_flag_key=res["recommended_flag_key"],
        flag_type=res["flag_type"],
        control_variation=res["control_variation"],
        treatment_variation=res["treatment_variation"],
        release_id=res.get("release_id"),
        release_key=res.get("release_key"),
        evidence_class=res.get("evidence_class", "AI_INFERRED"),
    ).model_dump(mode="json")


FLAG_TOOL_HANDLERS: dict[str, Any] = {
    "flagsmith_list_projects": handle_flagsmith_list_projects,
    "flagsmith_list_environments": handle_flagsmith_list_environments,
    "flagsmith_list_flags": handle_flagsmith_list_flags,
    "flagsmith_get_flag_state": handle_flagsmith_get_flag_state,
    "flagsmith_create_flag": handle_flagsmith_create_flag,
    "flagsmith_upsert_percentage_segment": handle_flagsmith_upsert_percentage_segment,
    "flagsmith_toggle_flag": handle_flagsmith_toggle_flag,
    "flagsmith_set_gradual_rollout_stage": handle_flagsmith_set_gradual_rollout_stage,
    "flagsmith_configure_multivariate_split": handle_flagsmith_configure_multivariate_split,
    "flagsmith_rollback_flag": handle_flagsmith_rollback_flag,
    "flagsmith_verify_live_state": handle_flagsmith_verify_live_state,
    "scan_flag_health": handle_scan_flag_health,
    "flagsmith_delete_or_archive_flag": handle_flagsmith_delete_or_archive_flag,
    "flagsmith_explain_identity": handle_flagsmith_explain_identity,
    "plan_feature_request_rollout": handle_plan_feature_request_rollout,
}


def get_flag_tool_names() -> list[str]:
    """Return ordered list of all 15 flag_agent tool names."""
    return list(FLAG_TOOL_HANDLERS.keys())
