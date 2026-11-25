"""Progressive Delivery Engine REST & SSE endpoints (/api/v1/rollouts)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthenticatedOperator,
    DBSession,
    DeveloperOperator,
    build_flagsmith_client,
)
from app.api.v1.errors import APIException
from app.models import (
    ActorType,
    GuardrailEvaluation,
    ProgressiveRollout,
    ProgressiveRolloutEvent,
    ProgressiveRolloutStage,
    ProgressiveRolloutStatus,
    RollbackOperation,
    RolloutDecision,
    RolloutGuardrail,
    RolloutMetricSnapshot,
)
from app.schemas import (
    GuardrailEvaluationOut,
    PaginatedResponse,
    ProgressiveRolloutCreateRequest,
    ProgressiveRolloutDetailOut,
    ProgressiveRolloutEventOut,
    ProgressiveRolloutOut,
    ProgressiveRolloutStageOut,
    RollbackOperationOut,
    RolloutControlRequest,
    RolloutDecisionOut,
    RolloutGuardrailOut,
    RolloutMetricSnapshotOut,
)
from app.services.progressive_delivery import (
    ProgressiveDeliveryError,
    ProgressiveDeliveryService,
)
from app.services.progressive_delivery.rollout_service import TERMINAL_ROLLOUT_STATES
from app.services.sse_bus import format_sse, sse_bus

rollouts_router = APIRouter(prefix="/rollouts", tags=["progressive-delivery"])


async def _load_rollout_or_404(
    session: AsyncSession, rollout_id: uuid.UUID
) -> ProgressiveRollout:
    obj = await session.get(ProgressiveRollout, rollout_id)
    if obj is None:
        raise APIException(
            status_code=404,
            code="rollout_not_found",
            message=f"Progressive rollout {rollout_id} not found.",
        )
    return obj


async def _build_detail(
    session: AsyncSession, rollout: ProgressiveRollout
) -> ProgressiveRolloutDetailOut:
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
    guardrails = list(
        (
            await session.execute(
                select(RolloutGuardrail).where(RolloutGuardrail.rollout_id == rollout.id)
            )
        )
        .scalars()
        .all()
    )
    metrics = list(
        (
            await session.execute(
                select(RolloutMetricSnapshot)
                .where(RolloutMetricSnapshot.rollout_id == rollout.id)
                .order_by(RolloutMetricSnapshot.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    evals = list(
        (
            await session.execute(
                select(GuardrailEvaluation)
                .where(GuardrailEvaluation.rollout_id == rollout.id)
                .order_by(GuardrailEvaluation.evaluated_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    decisions = list(
        (
            await session.execute(
                select(RolloutDecision)
                .where(RolloutDecision.rollout_id == rollout.id)
                .order_by(RolloutDecision.created_at.desc())
                .limit(30)
            )
        )
        .scalars()
        .all()
    )
    events = list(
        (
            await session.execute(
                select(ProgressiveRolloutEvent)
                .where(ProgressiveRolloutEvent.rollout_id == rollout.id)
                .order_by(ProgressiveRolloutEvent.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    rollbacks = list(
        (
            await session.execute(
                select(RollbackOperation)
                .where(RollbackOperation.rollout_id == rollout.id)
                .order_by(RollbackOperation.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return ProgressiveRolloutDetailOut(
        rollout=ProgressiveRolloutOut.model_validate(rollout),
        stages=[ProgressiveRolloutStageOut.model_validate(s) for s in stages],
        guardrails=[RolloutGuardrailOut.model_validate(g) for g in guardrails],
        metrics=[RolloutMetricSnapshotOut.model_validate(m) for m in metrics],
        evaluations=[GuardrailEvaluationOut.model_validate(e) for e in evals],
        decisions=[RolloutDecisionOut.model_validate(d) for d in decisions],
        events=[ProgressiveRolloutEventOut.model_validate(ev) for ev in events],
        rollbacks=[RollbackOperationOut.model_validate(rb) for rb in rollbacks],
    )


def _raise_pd_err(exc: ProgressiveDeliveryError) -> None:
    raise APIException(status_code=exc.status_code, code=exc.code, message=exc.message) from exc


@rollouts_router.post("", response_model=ProgressiveRolloutDetailOut, status_code=201)
async def create_rollout(
    body: ProgressiveRolloutCreateRequest,
    session: DBSession,
    operator: DeveloperOperator,
) -> ProgressiveRolloutDetailOut:
    try:
        rollout = await ProgressiveDeliveryService.create_rollout(
            session,
            name=body.name,
            description=body.description,
            feature_flag_id=body.feature_flag_id,
            environment=body.environment,
            release_id=body.release_id,
            experiment_id=body.experiment_id,
            stage_percentages=body.stage_percentages,
            primary_metric_key=body.primary_metric_key,
            observation_config=body.observation_config,
            guardrails_spec=body.guardrails,
            actor_id=operator.id,
        )
    except ProgressiveDeliveryError as exc:
        _raise_pd_err(exc)
    await session.commit()
    return await _build_detail(session, rollout)


@rollouts_router.get("", response_model=PaginatedResponse[ProgressiveRolloutOut])
async def list_rollouts(
    session: DBSession,
    _: AuthenticatedOperator,
    status: Annotated[ProgressiveRolloutStatus | None, Query()] = None,
    environment: Annotated[str | None, Query(max_length=64)] = None,
    release_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginatedResponse[ProgressiveRolloutOut]:
    base_q = select(ProgressiveRollout)
    count_q = select(func.count(ProgressiveRollout.id))
    if status is not None:
        base_q = base_q.where(ProgressiveRollout.status == status)
        count_q = count_q.where(ProgressiveRollout.status == status)
    if environment:
        env_n = environment.strip().lower()
        base_q = base_q.where(ProgressiveRollout.environment == env_n)
        count_q = count_q.where(ProgressiveRollout.environment == env_n)
    if release_id is not None:
        base_q = base_q.where(ProgressiveRollout.release_id == release_id)
        count_q = count_q.where(ProgressiveRollout.release_id == release_id)

    total = int((await session.execute(count_q)).scalar_one() or 0)
    rows = (
        await session.execute(
            base_q.order_by(ProgressiveRollout.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return PaginatedResponse[ProgressiveRolloutOut](
        items=[ProgressiveRolloutOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@rollouts_router.get("/{rollout_id}", response_model=ProgressiveRolloutDetailOut)
async def get_rollout_detail(
    rollout_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
) -> ProgressiveRolloutDetailOut:
    rollout = await _load_rollout_or_404(session, rollout_id)
    return await _build_detail(session, rollout)


@rollouts_router.post("/{rollout_id}/validate")
async def validate_rollout(
    rollout_id: uuid.UUID,
    session: DBSession,
    operator: DeveloperOperator,
) -> dict[str, Any]:
    rollout = await _load_rollout_or_404(session, rollout_id)
    res = await ProgressiveDeliveryService.validate_rollout(
        session, rollout, actor_id=operator.id
    )
    await session.commit()
    return res


@rollouts_router.post("/{rollout_id}/start", response_model=ProgressiveRolloutDetailOut)
async def start_rollout(
    rollout_id: uuid.UUID,
    request: Request,
    session: DBSession,
    operator: DeveloperOperator,
) -> ProgressiveRolloutDetailOut:
    rollout = await _load_rollout_or_404(session, rollout_id)
    flagsmith_client = await build_flagsmith_client(request.app, session)
    try:
        await ProgressiveDeliveryService.start_rollout(
            session, rollout, actor_id=operator.id, flagsmith_client=flagsmith_client
        )
    except ProgressiveDeliveryError as exc:
        _raise_pd_err(exc)
    await session.commit()
    return await _build_detail(session, rollout)


@rollouts_router.post(
    "/{rollout_id}/stages/{stage_id}/evaluate", response_model=RolloutDecisionOut
)
async def evaluate_rollout_stage(
    rollout_id: uuid.UUID,
    stage_id: uuid.UUID,
    session: DBSession,
    operator: DeveloperOperator,
) -> RolloutDecisionOut:
    rollout = await _load_rollout_or_404(session, rollout_id)
    stage = await session.get(ProgressiveRolloutStage, stage_id)
    if stage is None or stage.rollout_id != rollout_id:
        raise APIException(status_code=404, code="stage_not_found", message="Stage not found.")
    dec = await ProgressiveDeliveryService.evaluate_stage(
        session, rollout, stage, actor_id=operator.id
    )
    await session.commit()
    return RolloutDecisionOut.model_validate(dec)


@rollouts_router.post(
    "/{rollout_id}/stages/{stage_id}/advance", response_model=ProgressiveRolloutDetailOut
)
async def advance_rollout_stage(
    rollout_id: uuid.UUID,
    stage_id: uuid.UUID,
    request: Request,
    session: DBSession,
    operator: DeveloperOperator,
) -> ProgressiveRolloutDetailOut:
    del stage_id
    rollout = await _load_rollout_or_404(session, rollout_id)
    flagsmith_client = await build_flagsmith_client(request.app, session)
    try:
        await ProgressiveDeliveryService.advance_stage(
            session, rollout, actor_id=operator.id, flagsmith_client=flagsmith_client
        )
    except ProgressiveDeliveryError as exc:
        _raise_pd_err(exc)
    await session.commit()
    return await _build_detail(session, rollout)


@rollouts_router.post("/{rollout_id}/pause", response_model=ProgressiveRolloutDetailOut)
async def pause_rollout(
    rollout_id: uuid.UUID,
    body: RolloutControlRequest,
    session: DBSession,
    operator: DeveloperOperator,
) -> ProgressiveRolloutDetailOut:
    rollout = await _load_rollout_or_404(session, rollout_id)
    if rollout.status in TERMINAL_ROLLOUT_STATES:
        raise APIException(status_code=422, code="rollout_terminal", message="Rollout is terminal.")
    rollout.previous_status = rollout.status
    rollout.status = ProgressiveRolloutStatus.PAUSED
    rollout.version += 1
    await ProgressiveDeliveryService._record_event(
        session,
        rollout,
        event_type="paused",
        actor_id=operator.id,
        actor_type=ActorType.USER,
        message=body.reason or "Rollout paused by operator.",
    )
    await session.commit()
    return await _build_detail(session, rollout)


@rollouts_router.post("/{rollout_id}/resume", response_model=ProgressiveRolloutDetailOut)
async def resume_rollout(
    rollout_id: uuid.UUID,
    session: DBSession,
    operator: DeveloperOperator,
) -> ProgressiveRolloutDetailOut:
    rollout = await _load_rollout_or_404(session, rollout_id)
    if rollout.status != ProgressiveRolloutStatus.PAUSED:
        raise APIException(status_code=422, code="rollout_not_paused", message="Rollout is not paused.")
    rollout.status = rollout.previous_status or ProgressiveRolloutStatus.OBSERVING
    rollout.previous_status = None
    rollout.version += 1
    await ProgressiveDeliveryService._record_event(
        session,
        rollout,
        event_type="resumed",
        actor_id=operator.id,
        actor_type=ActorType.USER,
        message="Rollout resumed by operator.",
    )
    await session.commit()
    return await _build_detail(session, rollout)


@rollouts_router.post("/{rollout_id}/cancel", response_model=ProgressiveRolloutDetailOut)
async def cancel_rollout(
    rollout_id: uuid.UUID,
    body: RolloutControlRequest,
    session: DBSession,
    operator: DeveloperOperator,
) -> ProgressiveRolloutDetailOut:
    rollout = await _load_rollout_or_404(session, rollout_id)
    if rollout.status in TERMINAL_ROLLOUT_STATES:
        raise APIException(status_code=422, code="rollout_terminal", message="Rollout is terminal.")
    rollout.status = ProgressiveRolloutStatus.CANCELLED
    rollout.version += 1
    await ProgressiveDeliveryService._record_event(
        session,
        rollout,
        event_type="cancelled",
        actor_id=operator.id,
        actor_type=ActorType.USER,
        message=body.reason or "Rollout cancelled by operator.",
    )
    await session.commit()
    return await _build_detail(session, rollout)


@rollouts_router.post("/{rollout_id}/rollback/execute", response_model=RollbackOperationOut)
async def execute_rollout_rollback(
    rollout_id: uuid.UUID,
    body: RolloutControlRequest,
    request: Request,
    session: DBSession,
    operator: DeveloperOperator,
) -> RollbackOperationOut:
    rollout = await _load_rollout_or_404(session, rollout_id)
    flagsmith_client = await build_flagsmith_client(request.app, session)
    try:
        op = await ProgressiveDeliveryService.execute_rollback(
            session,
            rollout,
            actor_id=operator.id,
            reason=body.reason or "Operator-triggered rollback",
            flagsmith_client=flagsmith_client,
            has_production_approval=False,
        )
    except ProgressiveDeliveryError as exc:
        _raise_pd_err(exc)
    await session.commit()
    return RollbackOperationOut.model_validate(op)


@rollouts_router.get("/{rollout_id}/stream")
async def stream_rollout(
    rollout_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
) -> StreamingResponse:
    await _load_rollout_or_404(session, rollout_id)
    r_str = str(rollout_id)

    async def event_generator():
        async for evt in sse_bus.subscribe_workspace():
            payload: dict[str, Any] = dict(evt.get("payload") or {})
            if payload.get("rollout_id") == r_str or evt.get("event_type") in {
                "keepalive",
                "workspace.connected",
            }:
                yield format_sse(evt)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
