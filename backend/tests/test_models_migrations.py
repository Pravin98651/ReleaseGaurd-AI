"""Tests for config settings (44 env vars), Alembic SQLite migrations, 15 ORM models, and SSEBus."""

from __future__ import annotations

import asyncio
import uuid
import warnings
from datetime import timedelta
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alembic import command
from app.core.config import Settings, derive_dev_fernet_key
from app.core.database import utcnow
from app.models import (
    ActorType,
    AgentSession,
    AgentTask,
    AgentTaskStatus,
    AnalyticsMode,
    Approval,
    ApprovalCategory,
    AuditLog,
    AuditStatus,
    Base,
    CodeChange,
    CodeChangeType,
    Experiment,
    ExperimentEvent,
    ExperimentEventType,
    FeatureFlag,
    FlagType,
    IntegrationAuthMode,
    IntegrationConnection,
    IntegrationProvider,
    LangGraphCheckpoint,
    PolicyCategory,
    PullRequest,
    ReleaseRecommendation,
    Repository,
    RolloutAction,
    RolloutHistory,
    SpecialistRole,
    User,
    UserRole,
    WorkspaceSettings,
    default_guardrail_thresholds,
)
from app.schemas import TOOL_INPUT_SCHEMAS, TOOL_OUTPUT_SCHEMAS
from app.services.sse_bus import SSEBus, format_sse

EXPECTED_15_TABLES = {
    "users",
    "integration_connections",
    "repositories",
    "pull_requests",
    "agent_sessions",
    "agent_tasks",
    "code_changes",
    "feature_flags",
    "experiments",
    "experiment_events",
    "approvals",
    "rollout_history",
    "audit_logs",
    "langgraph_checkpoints",
    "workspace_settings",
}

EXPECTED_44_ENV_VARS = {
    "APP_ENV",
    "LOG_LEVEL",
    "BACKEND_HOST",
    "BACKEND_PORT",
    "CORS_ORIGINS",
    "SECRET_KEY",
    "ENCRYPTION_KEY",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "POSTGRES_PORT",
    "DATABASE_URL",
    "USE_SQLITE_FALLBACK",
    "SQLITE_FALLBACK_URL",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "GITHUB_CLIENT_ID",
    "GITHUB_CLIENT_SECRET",
    "GITHUB_OAUTH_REDIRECT_URI",
    "GITHUB_PAT",
    "FLAGSMITH_API_URL",
    "FLAGSMITH_EDGE_API_URL",
    "FLAGSMITH_ORG_TOKEN",
    "FLAGSMITH_PROJECT_ID",
    "FLAGSMITH_DEV_ENV_KEY",
    "FLAGSMITH_STAGING_ENV_KEY",
    "FLAGSMITH_PROD_ENV_KEY",
    "APPROVAL_TTL_SECONDS",
    "CATEGORY_B_REQUIRES_CONFIRMATION",
    "BAYESIAN_MC_SAMPLES",
    "BAYESIAN_MC_SEED",
    "BAYESIAN_MIN_EXPOSURES",
    "EVENT_INGEST_KEY",
    "PATCH_VALIDATION_TIMEOUT_SECONDS",
    "FRONTEND_PORT",
    "NEXT_PUBLIC_API_BASE_URL",
    "NEXT_PUBLIC_DEMO_APP_URL",
    "DEMO_BACKEND_PORT",
    "DEMO_FRONTEND_PORT",
    "NEXT_PUBLIC_DEMO_API_URL",
    "NEXT_PUBLIC_FLAGSMITH_ENV_KEY",
    "FLAGSMITH_SERVER_ENV_KEY",
    "RELEASEGUARD_API_URL",
    "RELEASEGUARD_INGEST_KEY",
}


def test_settings_covers_all_44_env_vars_and_db_resolver() -> None:
    model_fields = set(Settings.model_fields.keys())
    assert EXPECTED_44_ENV_VARS.issubset(model_fields)

    # Dev/test auto-derives a deterministic Fernet key and warns if omitted
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        dev_cfg = Settings(
            _env_file=None,
            APP_ENV="development",
            ENCRYPTION_KEY="",
            SECRET_KEY="unit-test-secret-key",
            USE_SQLITE_FALLBACK=True,
        )
    assert dev_cfg.encryption_key_derived is True
    assert dev_cfg.ENCRYPTION_KEY == derive_dev_fernet_key("unit-test-secret-key")
    assert any("ENCRYPTION_KEY is not set" in str(w.message) for w in caught)
    assert dev_cfg.is_sqlite is True
    assert dev_cfg.database_backend == "sqlite"

    pg_cfg = Settings(
        _env_file=None,
        APP_ENV="staging",
        ENCRYPTION_KEY=Fernet.generate_key().decode(),
        USE_SQLITE_FALLBACK=False,
        DATABASE_URL="postgresql+asyncpg://u:p@db:5432/rg",
    )
    assert pg_cfg.encryption_key_derived is False
    assert pg_cfg.is_sqlite is False
    assert pg_cfg.database_backend == "postgresql"
    assert pg_cfg.effective_database_url == "postgresql+asyncpg://u:p@db:5432/rg"


def test_settings_raises_in_production_without_encryption_key() -> None:
    with pytest.raises(ValueError, match="ENCRYPTION_KEY is required"):
        Settings(
            _env_file=None,
            APP_ENV="production",
            ENCRYPTION_KEY="",
            SECRET_KEY="strong-prod-secret-value-1234567890",
        )

    with pytest.raises(ValueError, match="valid 32-byte URL-safe base64 Fernet key"):
        Settings(
            _env_file=None,
            APP_ENV="development",
            ENCRYPTION_KEY="not-a-valid-fernet-key",
        )


def test_tool_schema_catalog_contains_all_36_tools() -> None:
    assert len(TOOL_INPUT_SCHEMAS) == 36
    assert len(TOOL_OUTPUT_SCHEMAS) == 36
    assert set(TOOL_INPUT_SCHEMAS.keys()) == set(TOOL_OUTPUT_SCHEMAS.keys())


def test_alembic_upgrade_downgrade_and_schema_parity_sqlite(tmp_path: Path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    db_file = tmp_path / "alembic_mig.db"
    async_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    sync_url = f"sqlite:///{db_file.as_posix()}"

    alembic_cfg = Config(str(backend_dir / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", async_url)

    command.upgrade(alembic_cfg, "head")

    sync_engine = create_engine(sync_url)
    try:
        inspector = inspect(sync_engine)
        tables = set(inspector.get_table_names())
        assert EXPECTED_15_TABLES.issubset(tables)

        # Verify zero schema drift between 0001_initial_schema.py and ORM Base.metadata
        with sync_engine.connect() as conn:
            mig_ctx = MigrationContext.configure(
                conn,
                opts={"compare_type": True, "render_as_batch": True},
            )
            diffs = compare_metadata(mig_ctx, Base.metadata)
            assert diffs == [], f"Unexpected metadata diffs: {diffs}"
    finally:
        sync_engine.dispose()

    command.downgrade(alembic_cfg, "base")
    sync_engine_after = create_engine(sync_url)
    try:
        remaining = set(inspect(sync_engine_after).get_table_names())
        assert EXPECTED_15_TABLES.isdisjoint(remaining)
    finally:
        sync_engine_after.dispose()

    command.upgrade(alembic_cfg, "head")


@pytest.mark.asyncio
async def test_all_15_models_roundtrip_and_constraints(db_session: AsyncSession) -> None:
    user = User(email="ops@releaseguard.ai", display_name="Ops Lead", role=UserRole.ADMIN)
    db_session.add(user)
    await db_session.flush()

    conn = IntegrationConnection(
        user_id=user.id,
        provider=IntegrationProvider.GITHUB,
        auth_mode=IntegrationAuthMode.PAT,
        external_account_id="octocat",
        external_account_name="octocat",
        scopes=["public_repo"],
        access_token_encrypted="gAAAAA_fake_cipher_for_model_test",
        token_hint="••••1234",
    )
    repo = Repository(
        connection=conn,
        github_repo_id=123456,
        owner="acme",
        name="novashop",
        full_name="acme/novashop",
        flagsmith_project_id=42,
    )
    pr = PullRequest(
        repository=repo,
        github_pr_number=7,
        title="Checkout v2",
        author_login="dev1",
        head_branch="feat/checkout-v2",
        base_branch="main",
        head_sha="abc123",
        html_url="https://github.com/acme/novashop/pull/7",
        risk_score=78,
    )
    session = AgentSession(
        user_id=user.id,
        thread_id="thread-model-1",
        active_specialist=SpecialistRole.PR_AGENT,
    )
    task = AgentTask(
        session=session,
        specialist=SpecialistRole.PR_AGENT,
        user_prompt="Analyze PR #7",
        status=AgentTaskStatus.RUNNING,
    )
    flag = FeatureFlag(
        flagsmith_feature_id=101,
        flagsmith_project_id=42,
        repository=repo,
        key="checkout_v2",
        flag_type=FlagType.STANDARD,
        health_score=85,
        health_issues=[
            {
                "issue_type": "stale",
                "penalty": -15,
                "summary": "Unchanged for >30 days",
                "evidence": {"days": 34},
            }
        ],
    )
    change = CodeChange(
        repository=repo,
        pull_request=pr,
        feature_flag=flag,
        agent_task=task,
        change_type=CodeChangeType.FLAG_WRAP,
        flag_key="checkout_v2",
        title="Wrap checkout_v2 behind Flagsmith",
    )
    exp = Experiment(
        feature_flag=flag,
        name="Checkout v2 Express Test",
        hypothesis="v2 increases completion without increasing payment failures",
        environment_name="staging",
        analytics_mode=AnalyticsMode.TRACKED_EVENT_FALLBACK,
        latest_recommendation=ReleaseRecommendation.EXPAND_ROLLOUT,
    )
    evt = ExperimentEvent(
        event_id="evt-001",
        experiment=exp,
        flag_key="checkout_v2",
        environment_name="staging",
        variation_key="treatment",
        event_type=ExperimentEventType.CHECKOUT_COMPLETED,
        user_identifier="user_001",
        numeric_value=99.95,
        occurred_at=utcnow(),
    )
    approval = Approval(
        agent_task=task,
        requested_by_id=user.id,
        policy_category=ApprovalCategory.C,
        action_type="flagsmith.set_gradual_rollout_stage",
        target_resource_type="feature_flag",
        target_resource_id=str(flag.id),
        environment_name="production",
        title="Roll out checkout_v2 to 25% in production",
        action_payload={"target_percentage": 25},
        action_hash="a" * 64,
        idempotency_key="idem-001",
        expires_at=utcnow() + timedelta(minutes=15),
    )
    rollout = RolloutHistory(
        feature_flag=flag,
        approval=approval,
        actor_id=user.id,
        environment_name="production",
        action=RolloutAction.ROLLOUT_STEP,
        previous_state={"enabled": False, "rollout_percentage": 10},
        new_state={"enabled": False, "rollout_percentage": 25},
        verified=True,
    )
    audit = AuditLog(
        actor_type=ActorType.USER,
        actor_id=str(user.id),
        policy_category=PolicyCategory.C,
        event_action="approval.created",
        resource_type="feature_flag",
        resource_id=str(flag.id),
        approval=approval,
        status=AuditStatus.PENDING,
    )
    ckpt = LangGraphCheckpoint(
        thread_id="thread-model-1",
        checkpoint_ns="",
        checkpoint_id="ckpt-001",
        checkpoint_json='{"v": 1}',
    )
    ws = WorkspaceSettings(singleton_key="default")

    db_session.add_all(
        [user, conn, repo, pr, session, task, flag, change, exp, evt, approval, rollout, audit, ckpt, ws]
    )
    await db_session.commit()

    # Check unified keys, aware UTC timestamps, and StrEnum deserialization
    loaded_exp = (await db_session.execute(select(Experiment))).scalar_one()
    assert loaded_exp.guardrail_thresholds == default_guardrail_thresholds()
    assert loaded_exp.latest_recommendation == ReleaseRecommendation.EXPAND_ROLLOUT
    assert loaded_exp.created_at.tzinfo is not None

    # Check CHECK constraint on PullRequest.risk_score (0..100)
    bad_pr = PullRequest(
        repository_id=repo.id,
        github_pr_number=999,
        title="Bad Score",
        author_login="dev2",
        head_branch="bad",
        base_branch="main",
        head_sha="deadbeef",
        html_url="https://github.com/acme/novashop/pull/999",
        risk_score=150,
    )
    db_session.add(bad_pr)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_sse_bus_publish_db_replay_and_live_stream(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as s1:
        session = AgentSession(thread_id=f"thr-{uuid.uuid4().hex[:8]}")
        task = AgentTask(
            session=session,
            specialist=SpecialistRole.FLAG_AGENT,
            user_prompt="Check flag status",
        )
        s1.add_all([session, task])
        await s1.commit()
        task_id = task.id

    bus = SSEBus()
    async with session_factory() as s_pub:
        ev1 = await bus.publish(
            task_id,
            "task.started",
            {"specialist": "flag_agent", "token": "ghp_superSecretTokenValue12345"},
            session=s_pub,
        )
        assert ev1["seq"] == 1
        assert ev1["payload"]["token"] == "[REDACTED]"

        ev2 = await bus.publish(
            task_id,
            "agent.thinking",
            {"message": "Checking Flagsmith staging state"},
            session=s_pub,
        )
        assert ev2["seq"] == 2

    # Subscribe with after_seq=1 -> should replay seq=2 from DB, then receive live seq=3
    received: list[dict] = []

    async def consumer() -> None:
        async with session_factory() as s_sub:
            async for item in bus.subscribe(
                task_id, after_seq=1, session=s_sub, heartbeat_seconds=0.5
            ):
                received.append(item)

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.05)

    async with session_factory() as s_pub2:
        await bus.publish(
            task_id,
            "agent.completed",
            {"final_response": "All good"},
            session=s_pub2,
        )

    await asyncio.wait_for(consumer_task, timeout=2.0)
    assert [item["seq"] for item in received] == [2, 3]
    assert received[1]["event_type"] == "agent.completed"

    sse_frame = format_sse(received[0])
    assert "id: 2\n" in sse_frame
    assert "event: agent.thinking\n" in sse_frame
    assert "data: " in sse_frame
