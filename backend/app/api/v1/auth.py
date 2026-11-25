"""Control Plane authentication, GitHub OAuth sign-in, session management, and RBAC user CRUD."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select

from app.api.deps import (
    AUTH_COOKIE_NAME,
    AdminOperator,
    AuthenticatedOperator,
    DBSession,
    build_github_client,
    is_auth_required,
    rate_limit_dependency,
    resolve_optional_token_user,
)
from app.api.v1.errors import APIException
from app.core.config import get_settings
from app.core.database import utcnow
from app.core.security import (
    hash_password,
    issue_auth_token,
    sign_oauth_state,
    verify_oauth_state,
    verify_password,
)
from app.integrations.github_client import GitHubClient
from app.models import (
    ActorType,
    AuditStatus,
    PolicyCategory,
    User,
    UserRole,
)
from app.schemas import (
    AuthLoginRequest,
    AuthStatusResponse,
    AuthTokenResponse,
    GitHubAuthCallbackRequest,
    GitHubOAuthStartResponse,
    UserCreateRequest,
    UserOut,
)
from app.services.policy_engine import PolicyEngine

auth_router = APIRouter(prefix="/auth", tags=["auth"])


def _set_auth_cookie(response: Response, token: str) -> None:
    cfg = get_settings()
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        max_age=cfg.AUTH_SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=(cfg.APP_ENV == "production"),
        path="/",
    )


def _issue_session_response(response: Response, user: User) -> AuthTokenResponse:
    cfg = get_settings()
    token = issue_auth_token(
        user_id=str(user.id),
        email=user.email,
        role=user.role.value,
        token_version=user.token_version,
    )
    _set_auth_cookie(response, token)
    return AuthTokenResponse(
        access_token=token,
        expires_in_seconds=cfg.AUTH_SESSION_TTL_SECONDS,
        user=UserOut.model_validate(user),
    )


@auth_router.get("/status", response_model=AuthStatusResponse)
async def get_auth_status(
    request: Request,
    session: DBSession,
) -> AuthStatusResponse:
    """Return whether Control Plane auth is enforced and resolve caller session if present."""
    cfg = get_settings()
    github_oauth_enabled = bool(cfg.GITHUB_CLIENT_ID.strip() and cfg.GITHUB_CLIENT_SECRET.strip())
    user: User | None = None
    try:
        user = await resolve_optional_token_user(request, session)
    except APIException:
        user = None

    return AuthStatusResponse(
        require_auth=is_auth_required(request),
        authenticated=user is not None,
        github_oauth_enabled=github_oauth_enabled,
        user=UserOut.model_validate(user) if user is not None else None,
    )


@auth_router.post(
    "/login",
    response_model=AuthTokenResponse,
    dependencies=[Depends(rate_limit_dependency("auth_login", max_requests=20, window_seconds=60))],
)
async def login_with_password(
    body: AuthLoginRequest,
    response: Response,
    session: DBSession,
) -> AuthTokenResponse:
    """Authenticate an operator with email and PBKDF2-verified password."""
    email_norm = body.email.strip().lower()
    user = (
        await session.execute(select(User).where(User.email == email_norm).limit(1))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise APIException(401, "INVALID_CREDENTIALS", "Invalid operator email or password.")

    cfg = get_settings()
    if user.password_hash:
        valid = verify_password(body.password, user.password_hash)
    elif (
        cfg.AUTH_OPERATOR_PASSWORD.strip()
        and email_norm == (cfg.AUTH_OPERATOR_EMAIL.strip().lower() or "operator@releaseguard.local")
        and body.password == cfg.AUTH_OPERATOR_PASSWORD.strip()
    ):
        user.password_hash = hash_password(body.password)
        valid = True
    else:
        valid = False

    if not valid:
        raise APIException(401, "INVALID_CREDENTIALS", "Invalid operator email or password.")

    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(user.id),
        policy_category=PolicyCategory.A,
        event_action="auth.login",
        resource_type="user",
        resource_id=str(user.id),
        status=AuditStatus.SUCCESS,
        details={"method": "password", "role": user.role.value},
    )
    await session.commit()
    await session.refresh(user)
    return _issue_session_response(response, user)


@auth_router.post("/logout")
async def logout_session(
    request: Request,
    response: Response,
    session: DBSession,
) -> dict[str, str]:
    """Invalidate active session token_version and clear the HttpOnly session cookie."""
    try:
        user = await resolve_optional_token_user(request, session)
    except APIException:
        user = None

    if user is not None:
        user.token_version = int(user.token_version) + 1
        user.updated_at = utcnow()
        await PolicyEngine.record_audit_log(
            session,
            actor_type=ActorType.USER,
            actor_id=str(user.id),
            policy_category=PolicyCategory.A,
            event_action="auth.logout",
            resource_type="user",
            resource_id=str(user.id),
            status=AuditStatus.SUCCESS,
            details={"role": user.role.value},
        )
        await session.commit()

    response.delete_cookie(key=AUTH_COOKIE_NAME, path="/")
    return {"status": "logged_out"}


@auth_router.get("/me", response_model=UserOut)
async def get_current_user_profile(
    operator: AuthenticatedOperator,
) -> UserOut:
    """Return authenticated operator identity and role."""
    return UserOut.model_validate(operator)


@auth_router.get("/github/start", response_model=GitHubOAuthStartResponse)
async def start_github_auth_login(request: Request) -> GitHubOAuthStartResponse:
    """Generate GitHub OAuth sign-in URL with CSRF-signed state."""
    cfg = get_settings()
    if not cfg.GITHUB_CLIENT_ID.strip():
        raise APIException(
            400,
            "GITHUB_OAUTH_NOT_CONFIGURED",
            "GITHUB_CLIENT_ID is not configured on the backend.",
        )
    state = sign_oauth_state({"purpose": "auth_login"})
    transport = getattr(request.app.state, "github_transport", None) if cfg.APP_ENV == "test" else None
    gh = GitHubClient(transport=transport)
    url = gh.build_oauth_authorize_url(
        client_id=cfg.GITHUB_CLIENT_ID,
        redirect_uri=cfg.GITHUB_OAUTH_REDIRECT_URI,
        state=state,
        include_private=False,
    )
    return GitHubOAuthStartResponse(authorize_url=url, state=state)


@auth_router.post("/github/callback", response_model=AuthTokenResponse)
async def complete_github_auth_callback(
    body: GitHubAuthCallbackRequest,
    request: Request,
    response: Response,
    session: DBSession,
) -> AuthTokenResponse:
    """Exchange GitHub OAuth code + CSRF state for an authenticated Control Plane session."""
    cfg = get_settings()
    if not cfg.GITHUB_CLIENT_ID.strip() or not cfg.GITHUB_CLIENT_SECRET.strip():
        raise APIException(
            400,
            "GITHUB_OAUTH_NOT_CONFIGURED",
            "GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET are required for GitHub OAuth login.",
        )
    try:
        verify_oauth_state(body.state)
    except ValueError as exc:
        raise APIException(400, "INVALID_OAUTH_STATE", str(exc)) from exc

    gh_base = await build_github_client(request.app, session, token="")
    token_data = await gh_base.exchange_oauth_code(
        client_id=cfg.GITHUB_CLIENT_ID,
        client_secret=cfg.GITHUB_CLIENT_SECRET,
        code=body.code,
        redirect_uri=cfg.GITHUB_OAUTH_REDIRECT_URI,
    )
    access_token = str(token_data.get("access_token") or "").strip()
    if not access_token:
        raise APIException(400, "GITHUB_OAUTH_NO_TOKEN", "GitHub did not return an access_token.")

    gh_authed = await build_github_client(request.app, session, token=access_token)
    verified = await gh_authed.verify_token()
    gh_user = verified["user"]
    gh_login = str(gh_user.get("login") or "").strip()
    email = str(gh_user.get("email") or f"{gh_login}@users.noreply.github.com").strip().lower()
    display_name = str(gh_user.get("name") or gh_login or email)

    existing = (
        await session.execute(
            select(User)
            .where((User.github_login == gh_login) | (User.email == email))
            .limit(1)
        )
    ).scalar_one_or_none()

    if existing is None:
        user_count = int((await session.execute(select(func.count(User.id)))).scalar_one() or 0)
        # First provisioned workspace operator becomes ADMIN; additional GitHub sign-ins default to ENGINEER
        assigned_role = UserRole.ADMIN if user_count == 0 else UserRole.ENGINEER
        existing = User(
            email=email,
            display_name=display_name[:120],
            role=assigned_role,
            github_login=gh_login[:120] if gh_login else None,
            is_active=True,
        )
        session.add(existing)
        await session.flush()
    else:
        if not existing.is_active:
            raise APIException(401, "USER_INACTIVE", "Operator account is disabled.")
        if gh_login and not existing.github_login:
            existing.github_login = gh_login[:120]
        existing.updated_at = utcnow()

    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(existing.id),
        policy_category=PolicyCategory.A,
        event_action="auth.github_login",
        resource_type="user",
        resource_id=str(existing.id),
        status=AuditStatus.SUCCESS,
        details={"github_login": gh_login, "role": existing.role.value},
    )
    await session.commit()
    await session.refresh(existing)
    return _issue_session_response(response, existing)


@auth_router.get("/users", response_model=list[UserOut])
async def list_workspace_users(
    session: DBSession,
    _: AdminOperator,
) -> list[UserOut]:
    """ADMIN-only: list all workspace operator accounts and RBAC roles."""
    rows = (await session.execute(select(User).order_by(User.created_at.asc()))).scalars().all()
    return [UserOut.model_validate(u) for u in rows]


@auth_router.post("/users", response_model=UserOut, status_code=201)
async def create_workspace_user(
    body: UserCreateRequest,
    session: DBSession,
    admin: AdminOperator,
) -> UserOut:
    """ADMIN-only: create an operator account with an explicit RBAC role."""
    email_norm = body.email.strip().lower()
    dup = (await session.execute(select(User).where(User.email == email_norm).limit(1))).scalar_one_or_none()
    if dup is not None:
        raise APIException(409, "USER_EXISTS", f"User '{email_norm}' already exists.")

    resolved_role = UserRole.ENGINEER if body.role == "developer" else UserRole(body.role)
    user = User(
        email=email_norm,
        display_name=body.display_name.strip(),
        role=resolved_role,
        password_hash=hash_password(body.password),
        is_active=True,
    )
    session.add(user)
    await session.flush()

    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(admin.id),
        policy_category=PolicyCategory.B,
        event_action="auth.user_created",
        resource_type="user",
        resource_id=str(user.id),
        status=AuditStatus.SUCCESS,
        details={"email": email_norm, "role": resolved_role.value},
    )
    await session.commit()
    await session.refresh(user)
    return UserOut.model_validate(user)
