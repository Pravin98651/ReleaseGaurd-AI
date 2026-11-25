"""Intelligent Release Risk Engine REST endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ApproverOperator,
    AuthenticatedOperator,
    DBSession,
    DeveloperOperator,
    build_gemini_client_from_app,
    build_github_client,
)
from app.api.v1.errors import APIException
from app.core.database import utcnow
from app.models import (
    MitigationStatus,
    ReleaseRiskAssessment,
    Repository,
    RiskDimensionResult,
    RiskEvidence,
    RiskFinding,
    RiskMitigation,
    RiskScenario,
)
from app.schemas import (
    PaginatedResponse,
    ReleaseRiskAssessmentOut,
    RiskAssessmentCreateRequest,
    RiskAssessmentDetailOut,
    RiskDimensionResultOut,
    RiskEngineConfigurationOut,
    RiskEngineConfigurationUpdateRequest,
    RiskEvidenceOut,
    RiskFindingOut,
    RiskMitigationCreateRequest,
    RiskMitigationOut,
    RiskMitigationUpdateRequest,
    RiskScenarioOut,
)
from app.services.policy_engine import PolicyEngine
from app.services.risk_engine import IntelligentReleaseRiskService, RiskEngineError
from app.services.risk_engine.analyzers_code_deps_hist import HistoricalFailureAnalyzer

risk_assessments_router = APIRouter(prefix="/risk-assessments", tags=["risk-engine"])
risk_engine_router = APIRouter(prefix="/risk-engine", tags=["risk-engine"])
repo_risk_router = APIRouter(prefix="/repositories", tags=["risk-engine"])


async def _load_assessment_or_404(
    session: AsyncSession, assessment_id: uuid.UUID
) -> ReleaseRiskAssessment:
    obj = await session.get(ReleaseRiskAssessment, assessment_id)
    if obj is None:
        raise APIException(
            status_code=404,
            code="risk_assessment_not_found",
            message=f"Risk assessment {assessment_id} not found.",
        )
    return obj


async def _build_detail(
    session: AsyncSession, assess: ReleaseRiskAssessment
) -> RiskAssessmentDetailOut:
    dims = list(
        (
            await session.execute(
                select(RiskDimensionResult).where(RiskDimensionResult.assessment_id == assess.id)
            )
        )
        .scalars()
        .all()
    )
    findings = list(
        (
            await session.execute(
                select(RiskFinding).where(RiskFinding.assessment_id == assess.id)
            )
        )
        .scalars()
        .all()
    )
    evidence = list(
        (
            await session.execute(
                select(RiskEvidence).where(RiskEvidence.assessment_id == assess.id)
            )
        )
        .scalars()
        .all()
    )
    mitigations = list(
        (
            await session.execute(
                select(RiskMitigation).where(RiskMitigation.assessment_id == assess.id)
            )
        )
        .scalars()
        .all()
    )
    scenarios = list(
        (
            await session.execute(
                select(RiskScenario).where(RiskScenario.assessment_id == assess.id)
            )
        )
        .scalars()
        .all()
    )
    return RiskAssessmentDetailOut(
        assessment=ReleaseRiskAssessmentOut.model_validate(assess),
        dimensions=[RiskDimensionResultOut.model_validate(d) for d in dims],
        findings=[RiskFindingOut.model_validate(f) for f in findings],
        evidence=[RiskEvidenceOut.model_validate(e) for e in evidence],
        mitigations=[RiskMitigationOut.model_validate(m) for m in mitigations],
        scenarios=[RiskScenarioOut.model_validate(s) for s in scenarios],
    )


@risk_assessments_router.post("", response_model=RiskAssessmentDetailOut, status_code=201)
async def create_risk_assessment(
    body: RiskAssessmentCreateRequest,
    request: Request,
    session: DBSession,
    operator: DeveloperOperator,
) -> RiskAssessmentDetailOut:
    ws = await PolicyEngine.get_workspace_settings(session)
    gemini_client = build_gemini_client_from_app(request.app, model_override=ws.gemini_model)
    github_client = await build_github_client(request.app, session)

    try:
        assess = await IntelligentReleaseRiskService.assess_release(
            session,
            release_id=body.release_id,
            pull_request_id=body.pull_request_id,
            repository_id=body.repository_id,
            target_environment=body.target_environment,
            actor_id=operator.id,
            github_client=github_client,
            gemini_client=gemini_client,
            files_override=body.files_override,
            coverage_report=body.coverage_report,
            create_execution=body.create_execution,
        )
    except RiskEngineError as exc:
        raise APIException(
            status_code=exc.status_code, code=exc.code, message=exc.message
        ) from exc

    await session.commit()
    return await _build_detail(session, assess)


@risk_assessments_router.get("", response_model=PaginatedResponse[ReleaseRiskAssessmentOut])
async def list_risk_assessments(
    session: DBSession,
    _: AuthenticatedOperator,
    release_id: Annotated[uuid.UUID | None, Query()] = None,
    pull_request_id: Annotated[uuid.UUID | None, Query()] = None,
    repository_id: Annotated[uuid.UUID | None, Query()] = None,
    current_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginatedResponse[ReleaseRiskAssessmentOut]:
    base_q = select(ReleaseRiskAssessment)
    count_q = select(func.count(ReleaseRiskAssessment.id))

    if release_id is not None:
        base_q = base_q.where(ReleaseRiskAssessment.release_id == release_id)
        count_q = count_q.where(ReleaseRiskAssessment.release_id == release_id)
    if pull_request_id is not None:
        base_q = base_q.where(ReleaseRiskAssessment.pull_request_id == pull_request_id)
        count_q = count_q.where(ReleaseRiskAssessment.pull_request_id == pull_request_id)
    if repository_id is not None:
        base_q = base_q.where(ReleaseRiskAssessment.repository_id == repository_id)
        count_q = count_q.where(ReleaseRiskAssessment.repository_id == repository_id)
    if current_only:
        base_q = base_q.where(ReleaseRiskAssessment.is_current.is_(True))
        count_q = count_q.where(ReleaseRiskAssessment.is_current.is_(True))

    total = int((await session.execute(count_q)).scalar_one() or 0)
    rows = (
        await session.execute(
            base_q.order_by(ReleaseRiskAssessment.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return PaginatedResponse[ReleaseRiskAssessmentOut](
        items=[ReleaseRiskAssessmentOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@risk_assessments_router.get("/{assessment_id}", response_model=RiskAssessmentDetailOut)
async def get_risk_assessment(
    assessment_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
) -> RiskAssessmentDetailOut:
    obj = await _load_assessment_or_404(session, assessment_id)
    return await _build_detail(session, obj)


@risk_assessments_router.get("/{assessment_id}/summary", response_model=ReleaseRiskAssessmentOut)
async def get_risk_assessment_summary(
    assessment_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
) -> ReleaseRiskAssessmentOut:
    obj = await _load_assessment_or_404(session, assessment_id)
    return ReleaseRiskAssessmentOut.model_validate(obj)


@risk_assessments_router.post("/{assessment_id}/reanalyze", response_model=RiskAssessmentDetailOut)
async def reanalyze_risk_assessment(
    assessment_id: uuid.UUID,
    request: Request,
    session: DBSession,
    operator: DeveloperOperator,
) -> RiskAssessmentDetailOut:
    prev = await _load_assessment_or_404(session, assessment_id)
    ws = await PolicyEngine.get_workspace_settings(session)
    gemini_client = build_gemini_client_from_app(request.app, model_override=ws.gemini_model)
    github_client = await build_github_client(request.app, session)

    assess = await IntelligentReleaseRiskService.assess_release(
        session,
        release_id=prev.release_id,
        pull_request_id=prev.pull_request_id,
        repository_id=prev.repository_id,
        target_environment=prev.target_environment,
        actor_id=operator.id,
        github_client=github_client,
        gemini_client=gemini_client,
    )
    await session.commit()
    return await _build_detail(session, assess)


@risk_assessments_router.get("/{assessment_id}/dimensions", response_model=list[RiskDimensionResultOut])
async def get_assessment_dimensions(
    assessment_id: uuid.UUID, session: DBSession, _: AuthenticatedOperator
) -> list[RiskDimensionResultOut]:
    await _load_assessment_or_404(session, assessment_id)
    rows = (
        await session.execute(
            select(RiskDimensionResult).where(RiskDimensionResult.assessment_id == assessment_id)
        )
    ).scalars().all()
    return [RiskDimensionResultOut.model_validate(r) for r in rows]


@risk_assessments_router.get("/{assessment_id}/findings", response_model=list[RiskFindingOut])
async def get_assessment_findings(
    assessment_id: uuid.UUID, session: DBSession, _: AuthenticatedOperator
) -> list[RiskFindingOut]:
    await _load_assessment_or_404(session, assessment_id)
    rows = (
        await session.execute(
            select(RiskFinding).where(RiskFinding.assessment_id == assessment_id)
        )
    ).scalars().all()
    return [RiskFindingOut.model_validate(r) for r in rows]


@risk_assessments_router.get("/{assessment_id}/evidence", response_model=list[RiskEvidenceOut])
async def get_assessment_evidence(
    assessment_id: uuid.UUID, session: DBSession, _: AuthenticatedOperator
) -> list[RiskEvidenceOut]:
    await _load_assessment_or_404(session, assessment_id)
    rows = (
        await session.execute(
            select(RiskEvidence).where(RiskEvidence.assessment_id == assessment_id)
        )
    ).scalars().all()
    return [RiskEvidenceOut.model_validate(r) for r in rows]


@risk_assessments_router.get("/{assessment_id}/scenarios", response_model=list[RiskScenarioOut])
async def get_assessment_scenarios(
    assessment_id: uuid.UUID, session: DBSession, _: AuthenticatedOperator
) -> list[RiskScenarioOut]:
    await _load_assessment_or_404(session, assessment_id)
    rows = (
        await session.execute(
            select(RiskScenario).where(RiskScenario.assessment_id == assessment_id)
        )
    ).scalars().all()
    return [RiskScenarioOut.model_validate(r) for r in rows]


@risk_assessments_router.get("/{assessment_id}/mitigations", response_model=list[RiskMitigationOut])
async def get_assessment_mitigations(
    assessment_id: uuid.UUID, session: DBSession, _: AuthenticatedOperator
) -> list[RiskMitigationOut]:
    await _load_assessment_or_404(session, assessment_id)
    rows = (
        await session.execute(
            select(RiskMitigation).where(RiskMitigation.assessment_id == assessment_id)
        )
    ).scalars().all()
    return [RiskMitigationOut.model_validate(r) for r in rows]


@risk_assessments_router.post(
    "/{assessment_id}/mitigations", response_model=RiskMitigationOut, status_code=201
)
async def add_assessment_mitigation(
    assessment_id: uuid.UUID,
    body: RiskMitigationCreateRequest,
    session: DBSession,
    _: DeveloperOperator,
) -> RiskMitigationOut:
    await _load_assessment_or_404(session, assessment_id)
    mit = RiskMitigation(
        assessment_id=assessment_id,
        dimension_key=body.dimension_key,
        title=body.title,
        description=body.description,
        priority=body.priority,
        mitigation_type=body.mitigation_type,
        required=body.required,
        verification_criteria=body.verification_criteria,
    )
    session.add(mit)
    await session.commit()
    return RiskMitigationOut.model_validate(mit)


@risk_assessments_router.patch(
    "/{assessment_id}/mitigations/{mitigation_id}", response_model=RiskMitigationOut
)
async def update_assessment_mitigation(
    assessment_id: uuid.UUID,
    mitigation_id: uuid.UUID,
    body: RiskMitigationUpdateRequest,
    session: DBSession,
    operator: DeveloperOperator,
) -> RiskMitigationOut:
    mit = await session.get(RiskMitigation, mitigation_id)
    if mit is None or mit.assessment_id != assessment_id:
        raise APIException(status_code=404, code="mitigation_not_found", message="Mitigation not found.")
    if body.status is not None:
        mit.status = body.status
        if body.status == MitigationStatus.ACCEPTED:
            mit.accepted_by_id = operator.id
        elif body.status == MitigationStatus.COMPLETED:
            mit.completed_by_id = operator.id
            mit.completed_at = utcnow()
    if body.verification_evidence is not None:
        mit.verification_evidence = body.verification_evidence
    await session.commit()
    return RiskMitigationOut.model_validate(mit)


@risk_assessments_router.post(
    "/{assessment_id}/mitigations/{mitigation_id}/verify", response_model=RiskMitigationOut
)
async def verify_assessment_mitigation(
    assessment_id: uuid.UUID,
    mitigation_id: uuid.UUID,
    body: RiskMitigationUpdateRequest,
    session: DBSession,
    operator: DeveloperOperator,
) -> RiskMitigationOut:
    mit = await session.get(RiskMitigation, mitigation_id)
    if mit is None or mit.assessment_id != assessment_id:
        raise APIException(status_code=404, code="mitigation_not_found", message="Mitigation not found.")
    mit.status = MitigationStatus.COMPLETED
    mit.completed_by_id = operator.id
    mit.completed_at = utcnow()
    if body.verification_evidence:
        mit.verification_evidence = body.verification_evidence
    await session.commit()
    return RiskMitigationOut.model_validate(mit)


@risk_engine_router.get("/configuration", response_model=RiskEngineConfigurationOut)
async def get_risk_engine_configuration(
    session: DBSession, _: AuthenticatedOperator
) -> RiskEngineConfigurationOut:
    cfg = await IntelligentReleaseRiskService.get_active_configuration(session)
    await session.commit()
    return RiskEngineConfigurationOut.model_validate(cfg)


@risk_engine_router.patch("/configuration", response_model=RiskEngineConfigurationOut)
async def update_risk_engine_configuration(
    body: RiskEngineConfigurationUpdateRequest,
    session: DBSession,
    operator: ApproverOperator,
) -> RiskEngineConfigurationOut:
    try:
        cfg = await IntelligentReleaseRiskService.update_configuration(
            session,
            actor_id=operator.id,
            dimension_weights=body.dimension_weights,
            risk_thresholds=body.risk_thresholds,
            critical_override_rules=body.critical_override_rules,
        )
    except RiskEngineError as exc:
        raise APIException(
            status_code=exc.status_code, code=exc.code, message=exc.message
        ) from exc
    await session.commit()
    return RiskEngineConfigurationOut.model_validate(cfg)


@repo_risk_router.get("/{repository_id}/dependency-graph")
async def get_repository_dependency_graph(
    repository_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
) -> dict[str, Any]:
    repo = await session.get(Repository, repository_id)
    if repo is None:
        raise APIException(status_code=404, code="repo_not_found", message="Repository not found.")
    latest = (
        await session.execute(
            select(ReleaseRiskAssessment)
            .where(ReleaseRiskAssessment.repository_id == repository_id)
            .order_by(ReleaseRiskAssessment.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return {
        "repository_id": str(repository_id),
        "assessment_id": str(latest.id) if latest else None,
        "dependency_graph": latest.dependency_graph if latest else {},
    }


@repo_risk_router.get("/{repository_id}/historical-risk")
async def get_repository_historical_risk(
    repository_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
) -> dict[str, Any]:
    repo = await session.get(Repository, repository_id)
    if repo is None:
        raise APIException(status_code=404, code="repo_not_found", message="Repository not found.")
    res = await HistoricalFailureAnalyzer.analyze(session, repository_id=repository_id)
    return {
        "repository_id": str(repository_id),
        "status": res.status.value,
        "data_sufficiency": res.data_sufficiency.value,
        "score": res.score,
        "explanations": res.explanations,
        "limitations": res.limitations,
        "details": res.details,
    }
