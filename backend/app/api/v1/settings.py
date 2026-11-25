"""Workspace settings and encrypted GitHub/Flagsmith integration endpoints."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AdminOperator,
    AuthenticatedOperator,
    DBSession,
    build_flagsmith_client,
    build_github_client,
)
from app.api.v1.errors import APIException
from app.core.config import get_settings
from app.core.database import utcnow
from app.core.security import (
    encrypt_token,
    make_token_hint,
    sign_oauth_state,
    verify_oauth_state,
)
from app.models import (
    ActorType,
    AnalyticsMode,
    AuditStatus,
    IntegrationAuthMode,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    PolicyCategory,
)

logger = logging.getLogger("releaseguard.api.settings")
from app.schemas import (
    FlagsmithConnectRequest,
    FlagsmithProbeRequest,
    FlagsmithProbeResponse,
    GitHubOAuthCallbackRequest,
    GitHubOAuthStartResponse,
    GitHubPatConnectRequest,
    GuardrailThresholdsSchema,
    HealthThresholdsSchema,
    IntegrationConnectionOut,
    WorkspaceSettingsOut,
    WorkspaceSettingsUpdate,
)
from app.services.policy_engine import PolicyEngine, is_production_environment

settings_router = APIRouter(prefix="/settings", tags=["settings"])
integrations_router = APIRouter(prefix="/integrations", tags=["integrations"])


async def _list_masked_integrations(session: AsyncSession) -> list[IntegrationConnectionOut]:
    rows = (
        (
            await session.execute(
                select(IntegrationConnection).order_by(IntegrationConnection.provider.asc())
            )
        )
        .scalars()
        .all()
    )
    return [IntegrationConnectionOut.model_validate(row) for row in rows]


def _serialize_workspace_settings(
    ws: Any,
    integrations: list[IntegrationConnectionOut],
) -> WorkspaceSettingsOut:
    return WorkspaceSettingsOut(
        id=ws.id,
        singleton_key=ws.singleton_key,
        category_b_requires_confirmation=ws.category_b_requires_confirmation,
        approval_ttl_seconds=ws.approval_ttl_seconds,
        gemini_model=ws.gemini_model,
        flagsmith_analytics_mode=ws.flagsmith_analytics_mode,
        flagsmith_capability_probed_at=ws.flagsmith_capability_probed_at,
        default_guardrails=GuardrailThresholdsSchema.model_validate(ws.default_guardrails or {}),
        health_thresholds=HealthThresholdsSchema.model_validate(ws.health_thresholds or {}),
        updated_at=ws.updated_at,
        integrations=integrations,
    )


async def _upsert_integration_connection(
    session: AsyncSession,
    *,
    user_id: Any,
    provider: IntegrationProvider,
    auth_mode: IntegrationAuthMode,
    external_account_id: str | None,
    external_account_name: str | None,
    scopes: list[str],
    plaintext_token: str,
    refresh_token: str | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> IntegrationConnection:
    stmt = select(IntegrationConnection).where(IntegrationConnection.provider == provider)
    if external_account_id is not None:
        stmt = stmt.where(IntegrationConnection.external_account_id == external_account_id)
    conn = (await session.execute(stmt.limit(1))).scalar_one_or_none()
    if conn is None:
        # Also reuse an existing disconnected connection for the same provider if present
        fallback_stmt = (
            select(IntegrationConnection)
            .where(IntegrationConnection.provider == provider)
            .order_by(IntegrationConnection.updated_at.desc())
            .limit(1)
        )
        conn = (await session.execute(fallback_stmt)).scalar_one_or_none()

    enc_access = encrypt_token(plaintext_token)
    enc_refresh = encrypt_token(refresh_token) if refresh_token else None
    hint = make_token_hint(plaintext_token)
    now = utcnow()

    if conn is None:
        conn = IntegrationConnection(
            user_id=user_id,
            provider=provider,
            auth_mode=auth_mode,
            status=IntegrationStatus.CONNECTED,
            external_account_id=external_account_id,
            external_account_name=external_account_name,
            scopes=scopes,
            access_token_encrypted=enc_access,
            refresh_token_encrypted=enc_refresh,
            token_hint=hint,
            metadata_json=metadata_json or {},
            last_verified_at=now,
        )
        session.add(conn)
    else:
        conn.user_id = user_id
        conn.auth_mode = auth_mode
        conn.status = IntegrationStatus.CONNECTED
        conn.external_account_id = external_account_id
        conn.external_account_name = external_account_name
        conn.scopes = scopes
        conn.access_token_encrypted = enc_access
        conn.refresh_token_encrypted = enc_refresh
        conn.token_hint = hint
        conn.metadata_json = metadata_json or {}
        conn.last_verified_at = now
        conn.updated_at = now

    await session.flush()
    return conn


@settings_router.get("", response_model=WorkspaceSettingsOut)
async def get_workspace_settings(
    session: DBSession,
    _: AuthenticatedOperator,
) -> WorkspaceSettingsOut:
    """Return workspace policy, Gemini model, thresholds, and masked integrations."""
    ws = await PolicyEngine.get_workspace_settings(session)
    await session.commit()
    integrations = await _list_masked_integrations(session)
    return _serialize_workspace_settings(ws, integrations)


@settings_router.patch("", response_model=WorkspaceSettingsOut)
async def update_workspace_settings(
    body: WorkspaceSettingsUpdate,
    session: DBSession,
    operator: AdminOperator,
) -> WorkspaceSettingsOut:
    """Update policy confirmation toggle, TTL, Gemini model, or thresholds."""
    ws = await PolicyEngine.get_workspace_settings(session)
    changed: dict[str, Any] = {}

    if body.category_b_requires_confirmation is not None:
        ws.category_b_requires_confirmation = body.category_b_requires_confirmation
        changed["category_b_requires_confirmation"] = body.category_b_requires_confirmation
    if body.approval_ttl_seconds is not None:
        ws.approval_ttl_seconds = body.approval_ttl_seconds
        changed["approval_ttl_seconds"] = body.approval_ttl_seconds
    if body.gemini_model is not None:
        ws.gemini_model = body.gemini_model.strip()
        changed["gemini_model"] = ws.gemini_model
    if body.default_guardrails is not None:
        ws.default_guardrails = body.default_guardrails.model_dump(mode="json")
        changed["default_guardrails"] = ws.default_guardrails
    if body.health_thresholds is not None:
        ws.health_thresholds = body.health_thresholds.model_dump(mode="json")
        changed["health_thresholds"] = ws.health_thresholds

    ws.updated_at = utcnow()
    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.B,
        event_action="settings.updated",
        resource_type="workspace_settings",
        resource_id=str(ws.id),
        status=AuditStatus.SUCCESS,
        details={"updated_fields": changed},
    )
    await session.commit()
    await session.refresh(ws)
    integrations = await _list_masked_integrations(session)
    return _serialize_workspace_settings(ws, integrations)


@integrations_router.get("", response_model=list[IntegrationConnectionOut])
async def list_integrations(
    session: DBSession,
    _: AuthenticatedOperator,
) -> list[IntegrationConnectionOut]:
    """List connected/disconnected integrations with masked token hints only."""
    return await _list_masked_integrations(session)


@integrations_router.get("/github/oauth/start", response_model=GitHubOAuthStartResponse)
async def start_github_oauth(
    request: Request,
    session: DBSession,
    _: AdminOperator,
    include_private: Annotated[bool, Query()] = False,
) -> GitHubOAuthStartResponse:
    """Start GitHub OAuth web flow with CSRF-signed HMAC state."""
    cfg = get_settings()
    if not cfg.GITHUB_CLIENT_ID.strip():
        raise APIException(
            400,
            "GITHUB_OAUTH_NOT_CONFIGURED",
            "GITHUB_CLIENT_ID is not configured. Use Fine-Grained PAT or configure GitHub OAuth.",
        )
    state = sign_oauth_state({"include_private": include_private})
    gh = await build_github_client(request.app, session, token="")
    url = gh.build_oauth_authorize_url(
        client_id=cfg.GITHUB_CLIENT_ID.strip(),
        redirect_uri=cfg.GITHUB_OAUTH_REDIRECT_URI,
        state=state,
        include_private=include_private,
    )
    return GitHubOAuthStartResponse(authorize_url=url, state=state)


@integrations_router.post("/github/oauth/callback", response_model=IntegrationConnectionOut)
async def complete_github_oauth_callback(
    request: Request,
    body: GitHubOAuthCallbackRequest,
    session: DBSession,
    operator: AdminOperator,
) -> IntegrationConnectionOut:
    """Verify OAuth state, exchange authorization code, and store Fernet-encrypted token."""
    try:
        state_payload = verify_oauth_state(body.state)
    except ValueError as exc:
        raise APIException(400, "INVALID_OAUTH_STATE", str(exc)) from exc

    cfg = get_settings()
    if not cfg.GITHUB_CLIENT_ID.strip() or not cfg.GITHUB_CLIENT_SECRET.strip():
        raise APIException(
            400,
            "GITHUB_OAUTH_NOT_CONFIGURED",
            "GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET must be set to exchange OAuth codes.",
        )

    gh_exchange = await build_github_client(request.app, session, token="")
    token_data = await gh_exchange.exchange_oauth_code(
        client_id=cfg.GITHUB_CLIENT_ID.strip(),
        client_secret=cfg.GITHUB_CLIENT_SECRET.strip(),
        code=body.code,
        redirect_uri=cfg.GITHUB_OAUTH_REDIRECT_URI,
    )
    access_token = str(token_data.get("access_token") or "")
    if not access_token:
        raise APIException(400, "GITHUB_OAUTH_FAILED", "GitHub OAuth response missing access_token.")

    gh_authed = await build_github_client(request.app, session, token=access_token)
    verified = await gh_authed.verify_token()
    user_info = verified["user"]
    scopes = verified["scopes"] or str(token_data.get("scope", "")).split(",")
    scopes = [s.strip() for s in scopes if s.strip()]

    conn = await _upsert_integration_connection(
        session,
        user_id=operator.id,
        provider=IntegrationProvider.GITHUB,
        auth_mode=IntegrationAuthMode.OAUTH,
        external_account_id=str(user_info.get("id") or user_info.get("login") or ""),
        external_account_name=str(user_info.get("login") or ""),
        scopes=scopes,
        plaintext_token=access_token,
        refresh_token=token_data.get("refresh_token"),
        metadata_json={"oauth_include_private": bool(state_payload.get("data", {}).get("include_private"))},
    )
    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.B,
        event_action="integration.github_connected",
        resource_type="integration_connection",
        resource_id=str(conn.id),
        status=AuditStatus.SUCCESS,
        details={"auth_mode": "oauth", "account": conn.external_account_name},
    )
    await session.commit()
    await session.refresh(conn)
    return IntegrationConnectionOut.model_validate(conn)


@integrations_router.post("/github/pat", response_model=IntegrationConnectionOut)
async def connect_github_pat(
    request: Request,
    body: GitHubPatConnectRequest,
    session: DBSession,
    operator: AdminOperator,
) -> IntegrationConnectionOut:
    """Verify GitHub Fine-Grained PAT, encrypt with Fernet, and persist connection."""
    gh = await build_github_client(request.app, session, token=body.token.strip())
    verified = await gh.verify_token()
    user_info = verified["user"]
    acct_name = body.account_label or str(user_info.get("login") or "github-operator")
    acct_id = str(user_info.get("id") or acct_name)

    conn = await _upsert_integration_connection(
        session,
        user_id=operator.id,
        provider=IntegrationProvider.GITHUB,
        auth_mode=IntegrationAuthMode.PAT,
        external_account_id=acct_id,
        external_account_name=acct_name,
        scopes=verified["scopes"],
        plaintext_token=body.token.strip(),
        metadata_json={"login": user_info.get("login"), "avatar_url": user_info.get("avatar_url")},
    )
    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.B,
        event_action="integration.github_connected",
        resource_type="integration_connection",
        resource_id=str(conn.id),
        status=AuditStatus.SUCCESS,
        details={"auth_mode": "pat", "account": acct_name},
    )
    await session.commit()
    await session.refresh(conn)
    return IntegrationConnectionOut.model_validate(conn)


@integrations_router.post("/flagsmith", response_model=IntegrationConnectionOut)
async def connect_flagsmith(
    request: Request,
    body: FlagsmithConnectRequest,
    session: DBSession,
    operator: AdminOperator,
) -> IntegrationConnectionOut:
    """Verify Flagsmith org token, detect v2 versioning, encrypt token, and probe experiments."""
    fs = await build_flagsmith_client(request.app, session, org_token=body.org_token.strip())
    envs = await fs.list_environments(body.project_id)

    env_keys: dict[str, str] = {
        str(k).strip().lower(): str(v).strip()
        for k, v in body.environment_keys.items()
        if str(v).strip()
    }
    env_details: dict[str, Any] = {}
    for env in envs:
        name = str(env.get("name", "")).strip().lower()
        api_key = str(env.get("api_key") or env_keys.get(name) or "").strip()
        if name and api_key:
            env_keys.setdefault(name, api_key)
        if name:
            env_details[name] = {
                "id": env.get("id"),
                "use_v2_feature_versioning": bool(env.get("use_v2_feature_versioning", False)),
                "is_production": bool(
                    env.get("is_production")
                    if "is_production" in env
                    else is_production_environment(name)
                ),
            }

    conn = await _upsert_integration_connection(
        session,
        user_id=operator.id,
        provider=IntegrationProvider.FLAGSMITH,
        auth_mode=IntegrationAuthMode.ORG_TOKEN,
        external_account_id=str(body.project_id),
        external_account_name=f"Flagsmith Project #{body.project_id}",
        scopes=["management_api"],
        plaintext_token=body.org_token.strip(),
        metadata_json={
            "project_id": body.project_id,
            "environment_keys": env_keys,
            "environments": env_details,
        },
    )

    # Probe Flagsmith experimentation capability on first available environment key
    probe_env_key = next(iter(env_keys.values()), None)
    if probe_env_key:
        try:
            probe = await fs.probe_experiment_capability(probe_env_key)
            ws = await PolicyEngine.get_workspace_settings(session)
            ws.flagsmith_analytics_mode = AnalyticsMode(probe["analytics_mode"])
            ws.flagsmith_capability_probed_at = utcnow()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Flagsmith initial capability probe failed: %s", exc)

    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.B,
        event_action="integration.flagsmith_connected",
        resource_type="integration_connection",
        resource_id=str(conn.id),
        status=AuditStatus.SUCCESS,
        details={"project_id": body.project_id, "environments": list(env_keys.keys())},
    )
    await session.commit()
    await session.refresh(conn)
    return IntegrationConnectionOut.model_validate(conn)


@integrations_router.post("/flagsmith/probe", response_model=FlagsmithProbeResponse)
async def probe_flagsmith_capability(
    request: Request,
    session: DBSession,
    _: AdminOperator,
    body: FlagsmithProbeRequest | None = None,
) -> FlagsmithProbeResponse:
    """Probe Flagsmith experimentation capability and update workspace analytics_mode."""
    fs = await build_flagsmith_client(request.app, session)
    env_key = body.environment_key.strip() if body and body.environment_key else ""

    if not env_key:
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
            if isinstance(env_map, dict) and env_map:
                env_key = str(next(iter(env_map.values())))

    if not env_key:
        cfg_keys = get_settings().flagsmith_env_keys
        env_key = next(iter(cfg_keys.values()), "")

    if not env_key:
        raise APIException(
            400,
            "FLAGSMITH_ENV_KEY_MISSING",
            "Connect Flagsmith with at least one environment key before probing experiments.",
        )

    probe = await fs.probe_experiment_capability(env_key)
    mode = AnalyticsMode(probe["analytics_mode"])
    now = utcnow()
    ws = await PolicyEngine.get_workspace_settings(session)
    ws.flagsmith_analytics_mode = mode
    ws.flagsmith_capability_probed_at = now
    await session.commit()

    return FlagsmithProbeResponse(
        analytics_mode=mode,
        native_supported=bool(probe.get("native_supported", False)),
        probed_at=now,
        details=dict(probe.get("details") or {}),
    )


@integrations_router.delete("/{provider}")
async def disconnect_integration(
    provider: str,
    session: DBSession,
    operator: AdminOperator,
) -> dict[str, Any]:
    """Disconnect a provider integration and revoke stored encrypted secrets."""
    try:
        prov_enum = IntegrationProvider(provider.strip().lower())
    except ValueError as exc:
        raise APIException(
            400, "INVALID_PROVIDER", f"Unsupported integration provider '{provider}'."
        ) from exc

    stmt = select(IntegrationConnection).where(IntegrationConnection.provider == prov_enum)
    rows = (await session.execute(stmt)).scalars().all()
    for conn in rows:
        conn.status = IntegrationStatus.DISCONNECTED
        conn.access_token_encrypted = "revoked"
        conn.refresh_token_encrypted = None
        conn.token_hint = "••••"
        conn.last_verified_at = None
        conn.updated_at = utcnow()

    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.B,
        event_action="integration.disconnected",
        resource_type="integration_connection",
        resource_id=prov_enum.value,
        status=AuditStatus.SUCCESS,
        details={"provider": prov_enum.value, "disconnected_count": len(rows)},
    )
    await session.commit()
    return {"provider": prov_enum.value, "status": "disconnected"}
