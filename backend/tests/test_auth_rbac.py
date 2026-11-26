"""Tests for Control Plane authentication, RBAC role matrix, session revocation, and production config hardening."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.security import (
    hash_password,
    issue_auth_token,
    verify_auth_token,
    verify_password,
)
from app.main import bootstrap_workspace_defaults, create_app
from app.models import (
    AuditLog,
    FeatureFlag,
    FlagType,
    Repository,
    User,
    UserRole,
)
from app.services.policy_engine import PolicyEngine


def _make_auth_test_app(
    async_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> Any:
    def gh_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_oauth_test_tok", "scope": "read:user"})
        if path == "/user":
            return httpx.Response(
                200,
                headers={"X-OAuth-Scopes": "read:user"},
                json={"id": 777, "login": "gh-engineer", "name": "GH Engineer", "email": "gh@acme.dev"},
            )
        if path == "/user/repos":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 9100,
                        "name": "checkout",
                        "full_name": "acme/checkout",
                        "owner": {"login": "acme"},
                        "default_branch": "main",
                        "private": False,
                        "language": "Python",
                    }
                ],
            )
        return httpx.Response(404, json={"message": f"Mock GH 404: {path}"})

    fake_genai = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=lambda **kwargs: SimpleNamespace(
                text="OK",
                function_calls=[],
                candidates=[],
                usage_metadata=SimpleNamespace(
                    prompt_token_count=5, candidates_token_count=2, total_token_count=7
                ),
            )
        )
    )
    app = create_app(
        engine=async_engine,
        session_factory=session_factory,
        github_transport=httpx.MockTransport(gh_handler),
        gemini_raw_client=fake_genai,
        bootstrap_env_tokens=False,
    )
    app.state.require_auth_override = True
    return app


def test_password_hashing_and_session_token_primitives() -> None:
    hashed = hash_password("S3cur3-Release-Pass!")
    assert hashed.startswith("pbkdf2_sha256$")
    assert verify_password("S3cur3-Release-Pass!", hashed) is True
    assert verify_password("wrong-pass", hashed) is False

    token = issue_auth_token(
        user_id="11111111-1111-1111-1111-111111111111",
        email="ops@releaseguard.ai",
        role="admin",
        token_version=2,
        secret_key="test-secret-key",
        ttl_seconds=120,
    )
    decoded = verify_auth_token(token, secret_key="test-secret-key")
    assert decoded["sub"] == "11111111-1111-1111-1111-111111111111"
    assert decoded["role"] == "admin"
    assert decoded["ver"] == 2

    with pytest.raises(ValueError, match="Invalid authentication token signature"):
        verify_auth_token(token[:-2] + "aa", secret_key="test-secret-key")

    expired = issue_auth_token(
        user_id="11111111-1111-1111-1111-111111111111",
        email="ops@releaseguard.ai",
        role="admin",
        secret_key="test-secret-key",
        ttl_seconds=-5,
    )
    with pytest.raises(ValueError, match="expired"):
        verify_auth_token(expired, secret_key="test-secret-key")


def test_production_config_rejects_default_ingest_key_and_disabled_auth() -> None:
    valid_fernet = Fernet.generate_key().decode()
    with pytest.raises(ValueError, match="EVENT_INGEST_KEY must be changed"):
        Settings(
            _env_file=None,
            APP_ENV="production",
            ENCRYPTION_KEY=valid_fernet,
            SECRET_KEY="strong-prod-secret-value-1234567890",
            EVENT_INGEST_KEY="dev-ingest-key-change-me",
            REQUIRE_AUTH=True,
        )

    with pytest.raises(ValueError, match="REQUIRE_AUTH must be enabled"):
        Settings(
            _env_file=None,
            APP_ENV="production",
            ENCRYPTION_KEY=valid_fernet,
            SECRET_KEY="strong-prod-secret-value-1234567890",
            EVENT_INGEST_KEY="custom-prod-ingest-key-xyz",
            REQUIRE_AUTH=False,
        )

    ok_cfg = Settings(
        _env_file=None,
        APP_ENV="production",
        ENCRYPTION_KEY=valid_fernet,
        SECRET_KEY="strong-prod-secret-value-1234567890",
        EVENT_INGEST_KEY="custom-prod-ingest-key-xyz",
        REQUIRE_AUTH=True,
    )
    assert ok_cfg.REQUIRE_AUTH is True


@pytest.mark.asyncio
async def test_auth_login_logout_github_oauth_and_rbac_enforcement(
    async_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = _make_auth_test_app(async_engine, session_factory)
    async with session_factory() as session:
        await bootstrap_workspace_defaults(session, bootstrap_env_tokens=False)
        admin_user = (
            await session.execute(select(User).where(User.role == UserRole.ADMIN).limit(1))
        ).scalar_one()
        admin_user.password_hash = hash_password("AdminPass!123")

        repo = Repository(
            github_repo_id=9100,
            owner="acme",
            name="checkout",
            full_name="acme/checkout",
            flagsmith_project_id=55,
        )
        flag = FeatureFlag(
            flagsmith_project_id=55,
            key="checkout_v2",
            flag_type=FlagType.STANDARD,
            env_states={"production": {"enabled": False, "rollout_percentage": 0, "is_production": True}},
        )
        session.add_all([repo, flag])
        await session.flush()

        payload = {
            "flag_key": "checkout_v2",
            "environment_name": "production",
            "target_percentage": 10,
            "strategy": "percentage_split_segment",
            "reason": "RBAC test",
        }
        approval = await PolicyEngine.create_approval(
            session,
            action_type="flagsmith_set_gradual_rollout_stage",
            target_resource_type="feature_flag",
            target_resource_id=str(flag.id),
            environment_name="production",
            title="Roll out checkout_v2 to 10%",
            rationale="RBAC rollout gate test",
            action_payload=payload,
            expected_precondition={
                "flag_key": "checkout_v2",
                "environment_name": "production",
                "enabled": False,
                "rollout_percentage": 0,
                "is_archived": False,
            },
            requested_by_id=admin_user.id,
        )
        await session.commit()
        approval_id = str(approval.id)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Public health + auth status allowed; protected API blocked (401)
        assert (await client.get("/health")).status_code == 200
        st = await client.get("/api/v1/auth/status")
        assert st.status_code == 200
        assert st.json()["require_auth"] is True
        assert st.json()["authenticated"] is False

        unauth_repos = await client.get("/api/v1/repositories")
        assert unauth_repos.status_code == 401
        assert unauth_repos.json()["error"]["code"] == "UNAUTHENTICATED"

        # 2. Invalid credentials rejected (401); valid ADMIN login issues token & cookie
        bad_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "operator@releaseguard.local", "password": "wrong-password"},
        )
        assert bad_login.status_code == 401

        ok_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "operator@releaseguard.local", "password": "AdminPass!123"},
        )
        assert ok_login.status_code == 200
        admin_tok = ok_login.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_tok}"}

        # 3. ADMIN creates VIEWER, ENGINEER (via "developer" alias), and RELEASE_MANAGER accounts
        for u_payload in [
            {"email": "viewer@acme.dev", "display_name": "Viewer", "password": "ViewerPass!1", "role": "viewer"},
            {"email": "dev@acme.dev", "display_name": "Dev", "password": "DevPass!123", "role": "developer"},
            {"email": "rm@acme.dev", "display_name": "ReleaseMgr", "password": "RmPass!1234", "role": "release_manager"},
        ]:
            cr = await client.post("/api/v1/auth/users", json=u_payload, headers=admin_headers)
            assert cr.status_code == 201, cr.text

        viewer_tok = (
            await client.post("/api/v1/auth/login", json={"email": "viewer@acme.dev", "password": "ViewerPass!1"})
        ).json()["access_token"]
        dev_tok = (
            await client.post("/api/v1/auth/login", json={"email": "dev@acme.dev", "password": "DevPass!123"})
        ).json()["access_token"]
        rm_tok = (
            await client.post("/api/v1/auth/login", json={"email": "rm@acme.dev", "password": "RmPass!1234"})
        ).json()["access_token"]

        # 4. VIEWER can read, but blocked (403) from writes & approvals
        assert (
            await client.get("/api/v1/repositories", headers={"Authorization": f"Bearer {viewer_tok}"})
        ).status_code == 200
        assert (
            await client.post("/api/v1/repositories/sync", headers={"Authorization": f"Bearer {viewer_tok}"})
        ).status_code == 403
        viewer_app = await client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            json={},
            headers={"Authorization": f"Bearer {viewer_tok}"},
        )
        assert viewer_app.status_code == 403
        assert viewer_app.json()["error"]["code"] == "FORBIDDEN_ROLE"

        # 5. ENGINEER/DEVELOPER can sync repos, but blocked (403) from approvals & settings mutations
        assert (
            await client.post("/api/v1/repositories/sync", headers={"Authorization": f"Bearer {dev_tok}"})
        ).status_code == 200
        assert (
            await client.patch(
                "/api/v1/settings",
                json={"approval_ttl_seconds": 300},
                headers={"Authorization": f"Bearer {dev_tok}"},
            )
        ).status_code == 403
        assert (
            await client.post(
                f"/api/v1/approvals/{approval_id}/approve",
                json={},
                headers={"Authorization": f"Bearer {dev_tok}"},
            )
        ).status_code == 403

        # 6. RELEASE_MANAGER cannot mutate admin settings (403), CAN reject/decide approvals
        assert (
            await client.patch(
                "/api/v1/settings",
                json={"approval_ttl_seconds": 300},
                headers={"Authorization": f"Bearer {rm_tok}"},
            )
        ).status_code == 403
        rm_rej = await client.post(
            f"/api/v1/approvals/{approval_id}/reject",
            json={"comment": "Hold rollout"},
            headers={"Authorization": f"Bearer {rm_tok}"},
        )
        assert rm_rej.status_code == 200
        assert rm_rej.json()["approval"]["status"] == "rejected"

        # 7. Logout revokes active bearer token immediately (token_version incremented)
        logout_resp = await client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {rm_tok}"})
        assert logout_resp.status_code == 200
        revoked_check = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {rm_tok}"}
        )
        assert revoked_check.status_code == 401
        assert revoked_check.json()["error"]["code"] == "SESSION_REVOKED"

        # Verify audit trail captured explicit RM login/rejection identity
        async with session_factory() as check_sess:
            logs = (
                await check_sess.execute(
                    select(AuditLog).where(AuditLog.event_action == "approval.rejected")
                )
            ).scalars().all()
            assert len(logs) >= 1
            rm_user = (
                await check_sess.execute(select(User).where(User.email == "rm@acme.dev"))
            ).scalar_one()
            assert logs[-1].actor_id == str(rm_user.id)


def test_startup_auth_hardening_create_all_guard_and_gemini_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.agents.gemini_client import GeminiClient
    from app.main import should_create_tables_on_startup, validate_startup_auth
    from app.models import WorkspaceSettings

    valid_fernet = Fernet.generate_key().decode()

    # 1. Default Gemini model is gemini-3.5-flash-lite across Settings, ORM, and GeminiClient
    dev_cfg = Settings(_env_file=None, APP_ENV="development", REQUIRE_AUTH=False)
    assert dev_cfg.GEMINI_MODEL == "gemini-3.5-flash-lite"
    assert WorkspaceSettings.gemini_model.default.arg == "gemini-3.5-flash-lite"
    assert GeminiClient(api_key="test-key").model == "gemini-3.5-flash-lite"

    # 2. REQUIRE_AUTH=false emits an explicit startup warning
    with caplog.at_level("WARNING", logger="releaseguard.main"):
        validate_startup_auth(dev_cfg, allow_test_bypass=False)
    assert any("REQUIRE_AUTH=false" in rec.message for rec in caplog.records)

    # 3. REQUIRE_AUTH=true without operator password or GitHub OAuth fails fast at startup
    unconfigured_auth_cfg = Settings(
        _env_file=None,
        APP_ENV="staging",
        ENCRYPTION_KEY=valid_fernet,
        REQUIRE_AUTH=True,
        AUTH_OPERATOR_PASSWORD="",
        GITHUB_CLIENT_ID="",
        GITHUB_CLIENT_SECRET="",
    )
    with pytest.raises(RuntimeError, match="REQUIRE_AUTH=true requires at least one sign-in method"):
        validate_startup_auth(unconfigured_auth_cfg, allow_test_bypass=False)

    # 4. Succeeds when AUTH_OPERATOR_PASSWORD or GitHub OAuth is configured
    pw_cfg = Settings(
        _env_file=None,
        APP_ENV="production",
        ENCRYPTION_KEY=valid_fernet,
        SECRET_KEY="strong-prod-secret-value-1234567890",
        EVENT_INGEST_KEY="custom-prod-ingest-key-xyz",
        REQUIRE_AUTH=True,
        AUTH_OPERATOR_PASSWORD="strong-operator-password",
    )
    validate_startup_auth(pw_cfg, allow_test_bypass=False)

    oauth_cfg = Settings(
        _env_file=None,
        APP_ENV="staging",
        ENCRYPTION_KEY=valid_fernet,
        REQUIRE_AUTH=True,
        GITHUB_CLIENT_ID="gh-client-id",
        GITHUB_CLIENT_SECRET="gh-client-secret",
    )
    validate_startup_auth(oauth_cfg, allow_test_bypass=False)

    # 5. Test bypass keeps create_app(APP_ENV='test') working cleanly
    test_cfg = Settings(_env_file=None, APP_ENV="test", REQUIRE_AUTH=True)
    validate_startup_auth(test_cfg, allow_test_bypass=True)

    # 6. Conditional create_all guard: skip for Alembic-managed Postgres, allow for dev SQLite/test
    pg_dev_cfg = Settings(
        _env_file=None,
        APP_ENV="development",
        USE_SQLITE_FALLBACK=False,
        DATABASE_URL="postgresql+asyncpg://u:p@postgres:5432/rg",
    )
    assert should_create_tables_on_startup(pg_dev_cfg, has_explicit_engine=False) is False
    assert should_create_tables_on_startup(pw_cfg, has_explicit_engine=False) is False
    sqlite_dev_cfg = Settings(_env_file=None, APP_ENV="development", USE_SQLITE_FALLBACK=True)
    assert should_create_tables_on_startup(sqlite_dev_cfg, has_explicit_engine=False) is True
    assert should_create_tables_on_startup(pg_dev_cfg, has_explicit_engine=True) is True
