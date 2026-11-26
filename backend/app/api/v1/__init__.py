"""V1 API router aggregator for ReleaseGuard AI."""

from fastapi import APIRouter

from app.api.v1.agent import agent_router
from app.api.v1.agent_executions import agent_executions_router
from app.api.v1.approvals import approvals_router
from app.api.v1.audit import audit_router
from app.api.v1.auth import auth_router
from app.api.v1.benchmarks import benchmarks_router
from app.api.v1.dx_integration import dx_router
from app.api.v1.events import events_router
from app.api.v1.experiments import experiments_router
from app.api.v1.flags import flag_health_router, flags_router
from app.api.v1.flagsmith_hub import flagsmith_hub_router
from app.api.v1.intelligence import intelligence_router
from app.api.v1.metrics import metrics_router
from app.api.v1.notifications import notifications_router
from app.api.v1.organization import org_router
from app.api.v1.progressive_delivery import rollouts_router
from app.api.v1.pull_requests import code_changes_router, pull_requests_router
from app.api.v1.releases import releases_router
from app.api.v1.repositories import repositories_router
from app.api.v1.risk_assessments import (
    repo_risk_router,
    risk_assessments_router,
    risk_engine_router,
)
from app.api.v1.schedules import schedules_router
from app.api.v1.settings import integrations_router, settings_router
from app.api.v1.system import dashboard_router, system_router, system_v1_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(auth_router)
api_v1_router.include_router(dashboard_router)
api_v1_router.include_router(metrics_router)
api_v1_router.include_router(benchmarks_router)
api_v1_router.include_router(settings_router)
api_v1_router.include_router(integrations_router)
api_v1_router.include_router(repositories_router)
api_v1_router.include_router(pull_requests_router)
api_v1_router.include_router(code_changes_router)
api_v1_router.include_router(agent_router)
api_v1_router.include_router(agent_executions_router)
api_v1_router.include_router(flags_router)
api_v1_router.include_router(flag_health_router)
api_v1_router.include_router(flagsmith_hub_router)
api_v1_router.include_router(releases_router)
api_v1_router.include_router(rollouts_router)
api_v1_router.include_router(intelligence_router)
api_v1_router.include_router(dx_router)
api_v1_router.include_router(org_router)
api_v1_router.include_router(risk_assessments_router)
api_v1_router.include_router(risk_engine_router)
api_v1_router.include_router(repo_risk_router)
api_v1_router.include_router(experiments_router)
api_v1_router.include_router(events_router)
api_v1_router.include_router(approvals_router)
api_v1_router.include_router(audit_router)
api_v1_router.include_router(schedules_router)
api_v1_router.include_router(notifications_router)
api_v1_router.include_router(system_v1_router)

__all__ = ["api_v1_router", "system_router"]
