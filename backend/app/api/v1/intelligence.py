"""Release Intelligence & Learning Engine REST API (Phase 5)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import (
    AdminOperator,
    ApproverOperator,
    AuthenticatedOperator,
    DBSession,
    DeveloperOperator,
)
from app.models import (
    AgentDecision,
    IntelligenceAnalysisRun,
    PolicyRecommendation,
    PolicyReviewStatus,
    RecommendationOutcome,
    ReleaseFailurePattern,
    ReleaseInsight,
    Repository,
    RepositoryIntelligenceProfile,
)
from app.schemas.common import PaginatedResponse
from app.schemas.intelligence import (
    IntelligenceAnalysisRunOut,
    IntelligenceAnalyzeRequest,
    IntelligenceQueryRequest,
    PolicyActionRequest,
    PolicyRecommendationOut,
    PolicyReviewRequest,
    RecommendationOutcomeOut,
    ReleaseFailurePatternOut,
    ReleaseInsightOut,
    RepositoryIntelligenceProfileOut,
)
from app.services.intelligence import (
    DependencyIntelligenceService,
    FailurePatternAnalyzer,
    HistoricalReleaseAnalyticsService,
    PolicyIntelligenceError,
    RecommendationEvaluationService,
    ReleaseKnowledgeBaseService,
    ReleaseLearningEngine,
    ReleasePolicyIntelligenceService,
    RepositoryIntelligenceService,
    RollbackIntelligenceService,
)

intelligence_router = APIRouter(prefix="/intelligence", tags=["intelligence"])


@intelligence_router.get("/overview")
async def get_intelligence_overview(
    session: DBSession,
    _user: AuthenticatedOperator,
    days: int = Query(default=30, ge=1, le=365),
    environment: str | None = None,
    repository_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    perf = await HistoricalReleaseAnalyticsService.compute_performance(
        session, repository_id=repository_id, environment=environment, days=days
    )
    trends = await HistoricalReleaseAnalyticsService.compute_trends(
        session, repository_id=repository_id, environment=environment, days=days
    )
    rollbacks = await RollbackIntelligenceService.analyze_rollbacks(session)
    rec_acc = await RecommendationEvaluationService.compute_accuracy_metrics(
        session, repository_id=repository_id
    )
    patterns = list(
        (
            await session.execute(
                select(ReleaseFailurePattern)
                .order_by(ReleaseFailurePattern.failure_count.desc())
                .limit(5)
            )
        )
        .scalars()
        .all()
    )
    insights = list(
        (
            await session.execute(
                select(ReleaseInsight).order_by(ReleaseInsight.updated_at.desc()).limit(5)
            )
        )
        .scalars()
        .all()
    )
    pending_policies = int(
        (
            await session.execute(
                select(func.count(PolicyRecommendation.id)).where(
                    PolicyRecommendation.review_status == PolicyReviewStatus.PENDING_REVIEW
                )
            )
        ).scalar_one()
        or 0
    )
    return {
        "performance": perf,
        "trend_direction": trends["trend_direction"],
        "previous_period": trends["previous_period"],
        "rollback_summary": {
            "total_rollbacks": rollbacks["total_rollbacks"],
            "top_causes": rollbacks["top_causes"][:5],
            "avg_recovery_seconds": rollbacks["avg_recovery_seconds"],
        },
        "recommendation_accuracy": rec_acc,
        "top_patterns": [ReleaseFailurePatternOut.model_validate(p) for p in patterns],
        "insights": [ReleaseInsightOut.model_validate(i) for i in insights],
        "pending_policy_recommendations": pending_policies,
    }


@intelligence_router.get("/release-performance")
async def get_release_performance(
    session: DBSession,
    _user: AuthenticatedOperator,
    days: int = Query(default=30, ge=1, le=365),
    environment: str | None = None,
    repository_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return await HistoricalReleaseAnalyticsService.compute_performance(
        session, repository_id=repository_id, environment=environment, days=days
    )


@intelligence_router.get("/trends")
async def get_release_trends(
    session: DBSession,
    _user: AuthenticatedOperator,
    days: int = Query(default=30, ge=1, le=365),
    environment: str | None = None,
    repository_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return await HistoricalReleaseAnalyticsService.compute_trends(
        session, repository_id=repository_id, environment=environment, days=days
    )


@intelligence_router.get("/failure-rates")
async def get_failure_rates(
    session: DBSession,
    _user: AuthenticatedOperator,
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    perf = await HistoricalReleaseAnalyticsService.compute_performance(session, days=days)
    return {
        "window_days": days,
        "qualifying_releases": perf["qualifying_releases"],
        "failed_releases": perf["failed_releases"],
        "change_failure_rate": perf["change_failure_rate"],
        "confidence_interval": perf["change_failure_rate_ci"],
        "rollback_rate": perf["rollback_rate"],
    }


@intelligence_router.get("/rollback-analysis")
@intelligence_router.get("/rollback-causes")
async def get_rollback_analysis(
    session: DBSession,
    _user: AuthenticatedOperator,
    category: str | None = None,
) -> dict[str, Any]:
    return await RollbackIntelligenceService.analyze_rollbacks(session, category=category)


@intelligence_router.get("/rollback-causes/{category}")
async def get_rollback_causes_by_category(
    category: str,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> dict[str, Any]:
    return await RollbackIntelligenceService.analyze_rollbacks(session, category=category)


@intelligence_router.get("/failure-patterns", response_model=PaginatedResponse[ReleaseFailurePatternOut])
async def list_failure_patterns(
    session: DBSession,
    _user: AuthenticatedOperator,
    repository_id: uuid.UUID | None = None,
    category: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[ReleaseFailurePatternOut]:
    stmt = select(ReleaseFailurePattern)
    count_stmt = select(func.count(ReleaseFailurePattern.id))
    if repository_id is not None:
        stmt = stmt.where(ReleaseFailurePattern.repository_id == repository_id)
        count_stmt = count_stmt.where(ReleaseFailurePattern.repository_id == repository_id)
    if category:
        stmt = stmt.where(ReleaseFailurePattern.category == category)
        count_stmt = count_stmt.where(ReleaseFailurePattern.category == category)
    total = int((await session.execute(count_stmt)).scalar_one() or 0)
    rows = list(
        (
            await session.execute(
                stmt.order_by(ReleaseFailurePattern.failure_count.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return PaginatedResponse(
        items=[ReleaseFailurePatternOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@intelligence_router.get("/failure-patterns/{pattern_id}", response_model=ReleaseFailurePatternOut)
async def get_failure_pattern(
    pattern_id: uuid.UUID,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> ReleaseFailurePatternOut:
    pat = await session.get(ReleaseFailurePattern, pattern_id)
    if pat is None:
        raise HTTPException(status_code=404, detail="Failure pattern not found.")
    return ReleaseFailurePatternOut.model_validate(pat)


@intelligence_router.post("/failure-patterns/analyze")
async def analyze_failure_patterns(
    session: DBSession,
    _user: DeveloperOperator,
    repository_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    patterns = await FailurePatternAnalyzer.analyze_patterns(session, repository_id=repository_id)
    await session.commit()
    return {
        "patterns_analyzed": len(patterns),
        "items": [ReleaseFailurePatternOut.model_validate(p) for p in patterns],
    }


@intelligence_router.get("/repositories", response_model=PaginatedResponse[RepositoryIntelligenceProfileOut])
async def list_repository_intelligence(
    session: DBSession,
    _user: AuthenticatedOperator,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[RepositoryIntelligenceProfileOut]:
    total = int((await session.execute(select(func.count(RepositoryIntelligenceProfile.id)))).scalar_one() or 0)
    rows = list(
        (
            await session.execute(
                select(RepositoryIntelligenceProfile)
                .order_by(RepositoryIntelligenceProfile.historical_failure_rate.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    items: list[RepositoryIntelligenceProfileOut] = []
    for r in rows:
        repo = await session.get(Repository, r.repository_id)
        out = RepositoryIntelligenceProfileOut.model_validate(r)
        out.repository_name = repo.full_name if repo else None
        items.append(out)
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@intelligence_router.get("/repositories/{repository_id}", response_model=RepositoryIntelligenceProfileOut)
async def get_repository_intelligence(
    repository_id: uuid.UUID,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> RepositoryIntelligenceProfileOut:
    repo = await session.get(Repository, repository_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found.")
    prof = (
        await session.execute(
            select(RepositoryIntelligenceProfile)
            .where(RepositoryIntelligenceProfile.repository_id == repository_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if prof is None:
        prof = await RepositoryIntelligenceService.refresh_profile(session, repo)
        await session.commit()
    out = RepositoryIntelligenceProfileOut.model_validate(prof)
    out.repository_name = repo.full_name
    return out


@intelligence_router.get("/repositories/{repository_id}/patterns")
async def get_repository_patterns(
    repository_id: uuid.UUID,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> dict[str, Any]:
    rows = list(
        (
            await session.execute(
                select(ReleaseFailurePattern).where(
                    ReleaseFailurePattern.repository_id == repository_id
                )
            )
        )
        .scalars()
        .all()
    )
    return {"items": [ReleaseFailurePatternOut.model_validate(r) for r in rows]}


@intelligence_router.get("/repositories/{repository_id}/recommendations")
async def get_repository_recommendations(
    repository_id: uuid.UUID,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> dict[str, Any]:
    prof = (
        await session.execute(
            select(RepositoryIntelligenceProfile)
            .where(RepositoryIntelligenceProfile.repository_id == repository_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    return {"items": prof.recommendations if prof else []}


@intelligence_router.get("/dependencies")
async def list_dependency_intelligence(
    session: DBSession,
    _user: AuthenticatedOperator,
) -> dict[str, Any]:
    items = await DependencyIntelligenceService.analyze_dependencies(session)
    return {"items": items, "total": len(items)}


@intelligence_router.get("/dependencies/{dependency_id}")
async def get_dependency_intelligence(
    dependency_id: str,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> dict[str, Any]:
    items = await DependencyIntelligenceService.analyze_dependencies(
        session, dependency_name=dependency_id
    )
    return {"items": items, "total": len(items)}


@intelligence_router.get("/recommendations", response_model=PaginatedResponse[RecommendationOutcomeOut])
async def list_recommendation_outcomes(
    session: DBSession,
    _user: AuthenticatedOperator,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[RecommendationOutcomeOut]:
    total = int((await session.execute(select(func.count(RecommendationOutcome.id)))).scalar_one() or 0)
    rows = list(
        (
            await session.execute(
                select(RecommendationOutcome)
                .order_by(RecommendationOutcome.evaluated_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return PaginatedResponse(
        items=[RecommendationOutcomeOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@intelligence_router.get("/recommendations/accuracy")
async def get_recommendation_accuracy(
    session: DBSession,
    _user: AuthenticatedOperator,
    repository_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return await RecommendationEvaluationService.compute_accuracy_metrics(
        session, repository_id=repository_id
    )


@intelligence_router.get("/recommendations/{recommendation_id}", response_model=RecommendationOutcomeOut)
async def get_recommendation_outcome(
    recommendation_id: uuid.UUID,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> RecommendationOutcomeOut:
    row = (
        await session.execute(
            select(RecommendationOutcome)
            .where(RecommendationOutcome.recommendation_id == recommendation_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Recommendation outcome not found.")
    return RecommendationOutcomeOut.model_validate(row)


@intelligence_router.post("/recommendations/{recommendation_id}/evaluate", response_model=RecommendationOutcomeOut)
async def evaluate_recommendation(
    recommendation_id: uuid.UUID,
    session: DBSession,
    _user: DeveloperOperator,
) -> RecommendationOutcomeOut:
    dec = await session.get(AgentDecision, recommendation_id)
    if dec is None:
        raise HTTPException(status_code=404, detail="Agent decision not found.")
    out = await RecommendationEvaluationService.evaluate_decision(session, dec)
    await session.commit()
    return RecommendationOutcomeOut.model_validate(out)


@intelligence_router.get("/policy-recommendations", response_model=PaginatedResponse[PolicyRecommendationOut])
async def list_policy_recommendations(
    session: DBSession,
    _user: AuthenticatedOperator,
    review_status: PolicyReviewStatus | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[PolicyRecommendationOut]:
    stmt = select(PolicyRecommendation)
    count_stmt = select(func.count(PolicyRecommendation.id))
    if review_status is not None:
        stmt = stmt.where(PolicyRecommendation.review_status == review_status)
        count_stmt = count_stmt.where(PolicyRecommendation.review_status == review_status)
    total = int((await session.execute(count_stmt)).scalar_one() or 0)
    rows = list(
        (
            await session.execute(
                stmt.order_by(PolicyRecommendation.created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return PaginatedResponse(
        items=[PolicyRecommendationOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@intelligence_router.get("/policy-recommendations/{recommendation_id}", response_model=PolicyRecommendationOut)
async def get_policy_recommendation(
    recommendation_id: uuid.UUID,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> PolicyRecommendationOut:
    rec = await session.get(PolicyRecommendation, recommendation_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Policy recommendation not found.")
    return PolicyRecommendationOut.model_validate(rec)


@intelligence_router.post("/policy-recommendations/{recommendation_id}/review", response_model=PolicyRecommendationOut)
async def review_policy_recommendation(
    recommendation_id: uuid.UUID,
    body: PolicyReviewRequest,
    session: DBSession,
    user: ApproverOperator,
) -> PolicyRecommendationOut:
    rec = await session.get(PolicyRecommendation, recommendation_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Policy recommendation not found.")
    try:
        updated = await ReleasePolicyIntelligenceService.review_recommendation(
            session,
            rec,
            reviewer_id=user.id,
            decision=body.decision,
            notes=body.notes,
            modified_configuration=body.modified_configuration,
        )
        await session.commit()
        return PolicyRecommendationOut.model_validate(updated)
    except PolicyIntelligenceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@intelligence_router.post("/policy-recommendations/{recommendation_id}/approve", response_model=PolicyRecommendationOut)
async def approve_policy_recommendation(
    recommendation_id: uuid.UUID,
    body: PolicyActionRequest,
    session: DBSession,
    user: ApproverOperator,
) -> PolicyRecommendationOut:
    rec = await session.get(PolicyRecommendation, recommendation_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Policy recommendation not found.")
    try:
        updated = await ReleasePolicyIntelligenceService.approve_recommendation(
            session, rec, approver_id=user.id, notes=body.notes
        )
        await session.commit()
        return PolicyRecommendationOut.model_validate(updated)
    except PolicyIntelligenceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@intelligence_router.post("/policy-recommendations/{recommendation_id}/implement", response_model=PolicyRecommendationOut)
async def implement_policy_recommendation(
    recommendation_id: uuid.UUID,
    session: DBSession,
    user: AdminOperator,
) -> PolicyRecommendationOut:
    rec = await session.get(PolicyRecommendation, recommendation_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Policy recommendation not found.")
    try:
        updated = await ReleasePolicyIntelligenceService.implement_recommendation(
            session, rec, actor_id=user.id
        )
        await session.commit()
        return PolicyRecommendationOut.model_validate(updated)
    except PolicyIntelligenceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@intelligence_router.post("/policy-recommendations/{recommendation_id}/verify", response_model=PolicyRecommendationOut)
async def verify_policy_recommendation(
    recommendation_id: uuid.UUID,
    session: DBSession,
    user: ApproverOperator,
) -> PolicyRecommendationOut:
    rec = await session.get(PolicyRecommendation, recommendation_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Policy recommendation not found.")
    try:
        updated = await ReleasePolicyIntelligenceService.verify_recommendation(
            session, rec, actor_id=user.id
        )
        await session.commit()
        return PolicyRecommendationOut.model_validate(updated)
    except PolicyIntelligenceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@intelligence_router.post("/query")
async def query_release_knowledge_base(
    body: IntelligenceQueryRequest,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> dict[str, Any]:
    return await ReleaseKnowledgeBaseService.query(
        session, question=body.question, repository_id=body.repository_id
    )


@intelligence_router.get("/knowledge")
async def get_release_knowledge_summary(
    session: DBSession,
    _user: AuthenticatedOperator,
    q: str = Query(default="What is our recent release performance?"),
    repository_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return await ReleaseKnowledgeBaseService.query(
        session, question=q, repository_id=repository_id
    )


@intelligence_router.post("/analyze", response_model=IntelligenceAnalysisRunOut)
async def trigger_intelligence_analysis(
    session: DBSession,
    _user: DeveloperOperator,
    body: IntelligenceAnalyzeRequest | None = None,
) -> IntelligenceAnalysisRunOut:
    req = body or IntelligenceAnalyzeRequest()
    run = await ReleaseLearningEngine.run_analysis(
        session, analysis_type=req.analysis_type, repository_id=req.repository_id
    )
    await session.commit()
    return IntelligenceAnalysisRunOut.model_validate(run)


@intelligence_router.get("/analysis-runs", response_model=PaginatedResponse[IntelligenceAnalysisRunOut])
async def list_analysis_runs(
    session: DBSession,
    _user: AuthenticatedOperator,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[IntelligenceAnalysisRunOut]:
    total = int((await session.execute(select(func.count(IntelligenceAnalysisRun.id)))).scalar_one() or 0)
    rows = list(
        (
            await session.execute(
                select(IntelligenceAnalysisRun)
                .order_by(IntelligenceAnalysisRun.started_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return PaginatedResponse(
        items=[IntelligenceAnalysisRunOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@intelligence_router.get("/analysis-runs/{run_id}", response_model=IntelligenceAnalysisRunOut)
async def get_analysis_run(
    run_id: uuid.UUID,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> IntelligenceAnalysisRunOut:
    run = await session.get(IntelligenceAnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Intelligence analysis run not found.")
    return IntelligenceAnalysisRunOut.model_validate(run)
