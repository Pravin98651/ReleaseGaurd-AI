"""V1 API router aggregator for ReleaseGuard AI."""

from fastapi import APIRouter

from app.api.v1.agent import agent_router
from app.api.v1.approvals import approvals_router
from app.api.v1.audit import audit_router
from app.api.v1.events import events_router
from app.api.v1.experiments import experiments_router
from app.api.v1.flags import flag_health_router, flags_router
from app.api.v1.pull_requests import code_changes_router, pull_requests_router
from app.api.v1.releases import releases_router
from app.api.v1.repositories import repositories_router
from app.api.v1.settings import integrations_router, settings_router
from app.api.v1.system import dashboard_router, system_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(dashboard_router)
api_v1_router.include_router(settings_router)
api_v1_router.include_router(integrations_router)
api_v1_router.include_router(repositories_router)
api_v1_router.include_router(pull_requests_router)
api_v1_router.include_router(code_changes_router)
api_v1_router.include_router(agent_router)
api_v1_router.include_router(flags_router)
api_v1_router.include_router(flag_health_router)
api_v1_router.include_router(releases_router)
api_v1_router.include_router(experiments_router)
api_v1_router.include_router(events_router)
api_v1_router.include_router(approvals_router)
api_v1_router.include_router(audit_router)

__all__ = ["api_v1_router", "system_router"]
