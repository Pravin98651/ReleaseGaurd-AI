"""Initial ReleaseGuard AI 15-table schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-25 14:40:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.database import JSONBCompat
from app.models.enums import (
    ActorType,
    AgentSessionStatus,
    AgentTaskStatus,
    AnalysisStatus,
    AnalyticsMode,
    ApprovalCategory,
    ApprovalStatus,
    AuditStatus,
    CodeChangeStatus,
    CodeChangeType,
    ExperimentEventType,
    ExperimentStatus,
    FlagType,
    IntegrationAuthMode,
    IntegrationProvider,
    IntegrationStatus,
    PolicyCategory,
    PullRequestState,
    ReleaseRecommendation,
    RiskLevel,
    RolloutAction,
    SpecialistRole,
    UserRole,
    ValidationStatus,
    str_enum,
)

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. users
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("role", str_enum(UserRole, length=50), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # 2. integration_connections
    op.create_table(
        "integration_connections",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("provider", str_enum(IntegrationProvider, length=50), nullable=False),
        sa.Column("auth_mode", str_enum(IntegrationAuthMode, length=50), nullable=False),
        sa.Column("status", str_enum(IntegrationStatus, length=50), nullable=False),
        sa.Column("external_account_id", sa.String(length=120), nullable=True),
        sa.Column("external_account_name", sa.String(length=255), nullable=True),
        sa.Column("scopes", JSONBCompat, nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("token_hint", sa.String(length=32), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("metadata_json", JSONBCompat, nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_integration_connections_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_integration_connections"),
        sa.UniqueConstraint(
            "provider", "external_account_id", name="uq_integration_provider_account"
        ),
    )
    op.create_index(
        "ix_integration_provider_status",
        "integration_connections",
        ["provider", "status"],
        unique=False,
    )

    # 3. repositories
    op.create_table(
        "repositories",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("connection_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("github_repo_id", sa.BigInteger(), nullable=False),
        sa.Column("owner", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("default_branch", sa.String(length=120), nullable=False),
        sa.Column("is_private", sa.Boolean(), nullable=False),
        sa.Column("flagsmith_project_id", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(length=64), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["integration_connections.id"],
            name="fk_repositories_connection_id_integration_connections",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_repositories"),
        sa.UniqueConstraint("full_name", name="uq_repositories_full_name"),
        sa.UniqueConstraint("github_repo_id", name="uq_repositories_github_repo_id"),
    )
    op.create_index("ix_repositories_full_name", "repositories", ["full_name"], unique=False)
    op.create_index("ix_repositories_owner_name", "repositories", ["owner", "name"], unique=False)

    # 4. pull_requests
    op.create_table(
        "pull_requests",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("repository_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("github_pr_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("author_login", sa.String(length=120), nullable=False),
        sa.Column("head_branch", sa.String(length=255), nullable=False),
        sa.Column("base_branch", sa.String(length=255), nullable=False),
        sa.Column("head_sha", sa.String(length=64), nullable=False),
        sa.Column("state", str_enum(PullRequestState, length=50), nullable=False),
        sa.Column("is_draft", sa.Boolean(), nullable=False),
        sa.Column("html_url", sa.String(length=500), nullable=False),
        sa.Column("analysis_status", str_enum(AnalysisStatus, length=50), nullable=False),
        sa.Column("risk_score", sa.Integer(), nullable=True),
        sa.Column("risk_level", str_enum(RiskLevel, length=50), nullable=True),
        sa.Column("analysis_summary", sa.Text(), nullable=True),
        sa.Column("confirmed_risks", JSONBCompat, nullable=False),
        sa.Column("potential_risks", JSONBCompat, nullable=False),
        sa.Column("flag_opportunities", JSONBCompat, nullable=False),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 100)",
            name="ck_pull_requests_risk_score_range",
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name="fk_pull_requests_repository_id_repositories",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_pull_requests"),
        sa.UniqueConstraint("repository_id", "github_pr_number", name="uq_pr_repo_number"),
    )
    op.create_index("ix_pr_analysis_status", "pull_requests", ["analysis_status"], unique=False)
    op.create_index("ix_pr_risk_level", "pull_requests", ["risk_level"], unique=False)

    # 5. agent_sessions
    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("thread_id", sa.String(length=128), nullable=False),
        sa.Column("active_specialist", str_enum(SpecialistRole, length=50), nullable=False),
        sa.Column("status", str_enum(AgentSessionStatus, length=50), nullable=False),
        sa.Column("context_scope", JSONBCompat, nullable=False),
        sa.Column("message_history", JSONBCompat, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_agent_sessions_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_sessions"),
    )
    op.create_index("ix_agent_sessions_thread_id", "agent_sessions", ["thread_id"], unique=True)
    op.create_index(
        "ix_agent_sessions_user_updated",
        "agent_sessions",
        ["user_id", "updated_at"],
        unique=False,
    )

    # 6. agent_tasks
    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("specialist", str_enum(SpecialistRole, length=50), nullable=False),
        sa.Column("user_prompt", sa.Text(), nullable=False),
        sa.Column("status", str_enum(AgentTaskStatus, length=50), nullable=False),
        sa.Column("steps_log", JSONBCompat, nullable=False),
        sa.Column("final_response", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("gemini_model", sa.String(length=100), nullable=True),
        sa.Column("token_usage", JSONBCompat, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_sessions.id"],
            name="fk_agent_tasks_session_id_agent_sessions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_tasks"),
    )
    op.create_index(
        "ix_agent_tasks_session_created", "agent_tasks", ["session_id", "created_at"], unique=False
    )
    op.create_index("ix_agent_tasks_status", "agent_tasks", ["status"], unique=False)

    # 7. feature_flags
    op.create_table(
        "feature_flags",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("flagsmith_feature_id", sa.Integer(), nullable=True),
        sa.Column("flagsmith_project_id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("flag_type", str_enum(FlagType, length=50), nullable=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("env_states", JSONBCompat, nullable=False),
        sa.Column("mv_options", JSONBCompat, nullable=False),
        sa.Column("code_references", JSONBCompat, nullable=False),
        sa.Column("health_score", sa.Integer(), nullable=False),
        sa.Column("health_issues", JSONBCompat, nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_health_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("flagsmith_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "health_score >= 0 AND health_score <= 100",
            name="ck_feature_flags_health_score_range",
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name="fk_feature_flags_repository_id_repositories",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_feature_flags"),
        sa.UniqueConstraint("flagsmith_project_id", "key", name="uq_flag_project_key"),
    )
    op.create_index("ix_flag_health_score", "feature_flags", ["health_score"], unique=False)

    # 8. code_changes
    op.create_table(
        "code_changes",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("repository_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("pull_request_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("feature_flag_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("agent_task_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("change_type", str_enum(CodeChangeType, length=50), nullable=False),
        sa.Column("flag_key", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("files_diff", JSONBCompat, nullable=False),
        sa.Column("generated_tests", JSONBCompat, nullable=False),
        sa.Column("validation_status", str_enum(ValidationStatus, length=50), nullable=False),
        sa.Column("validation_report", JSONBCompat, nullable=False),
        sa.Column("draft_pr_number", sa.Integer(), nullable=True),
        sa.Column("draft_pr_url", sa.String(length=500), nullable=True),
        sa.Column("draft_branch_name", sa.String(length=255), nullable=True),
        sa.Column("status", str_enum(CodeChangeStatus, length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agent_task_id"],
            ["agent_tasks.id"],
            name="fk_code_changes_agent_task_id_agent_tasks",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["feature_flag_id"],
            ["feature_flags.id"],
            name="fk_code_changes_feature_flag_id_feature_flags",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["pull_request_id"],
            ["pull_requests.id"],
            name="fk_code_changes_pull_request_id_pull_requests",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name="fk_code_changes_repository_id_repositories",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_code_changes"),
    )
    op.create_index("ix_code_changes_flag", "code_changes", ["feature_flag_id"], unique=False)
    op.create_index("ix_code_changes_pr", "code_changes", ["pull_request_id"], unique=False)
    op.create_index(
        "ix_code_changes_repo_status", "code_changes", ["repository_id", "status"], unique=False
    )

    # 9. experiments
    op.create_table(
        "experiments",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("feature_flag_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("environment_name", sa.String(length=64), nullable=False),
        sa.Column("environment_key", sa.String(length=128), nullable=True),
        sa.Column("analytics_mode", str_enum(AnalyticsMode, length=50), nullable=False),
        sa.Column("flagsmith_experiment_id", sa.String(length=128), nullable=True),
        sa.Column("status", str_enum(ExperimentStatus, length=50), nullable=False),
        sa.Column("control_variation", sa.String(length=64), nullable=False),
        sa.Column("treatment_variation", sa.String(length=64), nullable=False),
        sa.Column("traffic_allocation", JSONBCompat, nullable=False),
        sa.Column("primary_metric", sa.String(length=64), nullable=False),
        sa.Column("guardrail_metrics", JSONBCompat, nullable=False),
        sa.Column("guardrail_thresholds", JSONBCompat, nullable=False),
        sa.Column("latest_bayesian_result", JSONBCompat, nullable=True),
        sa.Column(
            "latest_recommendation", str_enum(ReleaseRecommendation, length=50), nullable=True
        ),
        sa.Column("recommendation_rationale", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["feature_flag_id"],
            ["feature_flags.id"],
            name="fk_experiments_feature_flag_id_feature_flags",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_experiments"),
    )
    op.create_index("ix_experiments_env", "experiments", ["environment_name"], unique=False)
    op.create_index(
        "ix_experiments_flag_status", "experiments", ["feature_flag_id", "status"], unique=False
    )

    # 10. experiment_events
    op.create_table(
        "experiment_events",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("experiment_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("flag_key", sa.String(length=128), nullable=False),
        sa.Column("environment_name", sa.String(length=64), nullable=False),
        sa.Column("variation_key", sa.String(length=64), nullable=False),
        sa.Column("event_type", str_enum(ExperimentEventType, length=50), nullable=False),
        sa.Column("user_identifier", sa.String(length=128), nullable=False),
        sa.Column("session_key", sa.String(length=128), nullable=True),
        sa.Column("numeric_value", sa.Double(), nullable=True),
        sa.Column("properties", JSONBCompat, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.id"],
            name="fk_experiment_events_experiment_id_experiments",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_experiment_events"),
        sa.UniqueConstraint("event_id", name="uq_experiment_events_event_id"),
    )
    op.create_index(
        "ix_exp_events_exp_var_type",
        "experiment_events",
        ["experiment_id", "variation_key", "event_type"],
        unique=False,
    )
    op.create_index(
        "ix_exp_events_flag_env_time",
        "experiment_events",
        ["flag_key", "environment_name", "occurred_at"],
        unique=False,
    )
    op.create_index("ix_exp_events_user", "experiment_events", ["user_identifier"], unique=False)

    # 11. approvals
    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("agent_task_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("requested_by_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("decided_by_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("policy_category", str_enum(ApprovalCategory, length=10), nullable=False),
        sa.Column("action_type", sa.String(length=100), nullable=False),
        sa.Column("target_resource_type", sa.String(length=64), nullable=False),
        sa.Column("target_resource_id", sa.String(length=128), nullable=False),
        sa.Column("environment_name", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("action_payload", JSONBCompat, nullable=False),
        sa.Column("expected_precondition", JSONBCompat, nullable=False),
        sa.Column("action_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", str_enum(ApprovalStatus, length=50), nullable=False),
        sa.Column("decision_comment", sa.Text(), nullable=True),
        sa.Column("execution_result", JSONBCompat, nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agent_task_id"],
            ["agent_tasks.id"],
            name="fk_approvals_agent_task_id_agent_tasks",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_id"],
            ["users.id"],
            name="fk_approvals_decided_by_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"],
            ["users.id"],
            name="fk_approvals_requested_by_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_approvals"),
        sa.UniqueConstraint("idempotency_key", name="uq_approvals_idempotency_key"),
    )
    op.create_index("ix_approvals_action_hash", "approvals", ["action_hash"], unique=False)
    op.create_index(
        "ix_approvals_status_expires", "approvals", ["status", "expires_at"], unique=False
    )
    op.create_index("ix_approvals_task", "approvals", ["agent_task_id"], unique=False)

    # 12. rollout_history
    op.create_table(
        "rollout_history",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("feature_flag_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("approval_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("environment_name", sa.String(length=64), nullable=False),
        sa.Column("action", str_enum(RolloutAction, length=50), nullable=False),
        sa.Column("previous_state", JSONBCompat, nullable=False),
        sa.Column("new_state", JSONBCompat, nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("verification_details", JSONBCompat, nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_rollout_history_actor_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["approvals.id"],
            name="fk_rollout_history_approval_id_approvals",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["feature_flag_id"],
            ["feature_flags.id"],
            name="fk_rollout_history_feature_flag_id_feature_flags",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_rollout_history"),
    )
    op.create_index("ix_rollout_created", "rollout_history", ["created_at"], unique=False)
    op.create_index(
        "ix_rollout_flag_env_time",
        "rollout_history",
        ["feature_flag_id", "environment_name", "created_at"],
        unique=False,
    )

    # 13. audit_logs
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("actor_type", str_enum(ActorType, length=50), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("policy_category", str_enum(PolicyCategory, length=10), nullable=False),
        sa.Column("event_action", sa.String(length=120), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        sa.Column("environment_name", sa.String(length=64), nullable=True),
        sa.Column("approval_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("action_hash", sa.CHAR(length=64), nullable=True),
        sa.Column("status", str_enum(AuditStatus, length=50), nullable=False),
        sa.Column("details", JSONBCompat, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["approvals.id"],
            name="fk_audit_logs_approval_id_approvals",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )
    op.create_index(
        "ix_audit_category_action",
        "audit_logs",
        ["policy_category", "event_action"],
        unique=False,
    )
    op.create_index("ix_audit_created", "audit_logs", ["created_at"], unique=False)
    op.create_index(
        "ix_audit_resource", "audit_logs", ["resource_type", "resource_id"], unique=False
    )

    # 14. langgraph_checkpoints
    op.create_table(
        "langgraph_checkpoints",
        sa.Column("thread_id", sa.String(length=128), nullable=False),
        sa.Column(
            "checkpoint_ns", sa.String(length=128), server_default="", nullable=False
        ),
        sa.Column("checkpoint_id", sa.String(length=128), nullable=False),
        sa.Column("parent_checkpoint_id", sa.String(length=128), nullable=True),
        sa.Column("checkpoint_json", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("pending_writes_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "thread_id", "checkpoint_ns", "checkpoint_id", name="pk_langgraph_checkpoints"
        ),
    )
    op.create_index(
        "ix_lg_ckpt_thread_created",
        "langgraph_checkpoints",
        ["thread_id", "checkpoint_ns", "created_at"],
        unique=False,
    )

    # 15. workspace_settings
    op.create_table(
        "workspace_settings",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("singleton_key", sa.String(length=32), nullable=False),
        sa.Column("category_b_requires_confirmation", sa.Boolean(), nullable=False),
        sa.Column("approval_ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("gemini_model", sa.String(length=100), nullable=False),
        sa.Column("flagsmith_analytics_mode", str_enum(AnalyticsMode, length=50), nullable=False),
        sa.Column("flagsmith_capability_probed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("default_guardrails", JSONBCompat, nullable=False),
        sa.Column("health_thresholds", JSONBCompat, nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workspace_settings"),
        sa.UniqueConstraint("singleton_key", name="uq_workspace_settings_singleton_key"),
    )


def downgrade() -> None:
    op.drop_table("workspace_settings")
    op.drop_index("ix_lg_ckpt_thread_created", table_name="langgraph_checkpoints")
    op.drop_table("langgraph_checkpoints")
    op.drop_index("ix_audit_resource", table_name="audit_logs")
    op.drop_index("ix_audit_created", table_name="audit_logs")
    op.drop_index("ix_audit_category_action", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_rollout_flag_env_time", table_name="rollout_history")
    op.drop_index("ix_rollout_created", table_name="rollout_history")
    op.drop_table("rollout_history")
    op.drop_index("ix_approvals_task", table_name="approvals")
    op.drop_index("ix_approvals_status_expires", table_name="approvals")
    op.drop_index("ix_approvals_action_hash", table_name="approvals")
    op.drop_table("approvals")
    op.drop_index("ix_exp_events_user", table_name="experiment_events")
    op.drop_index("ix_exp_events_flag_env_time", table_name="experiment_events")
    op.drop_index("ix_exp_events_exp_var_type", table_name="experiment_events")
    op.drop_table("experiment_events")
    op.drop_index("ix_experiments_flag_status", table_name="experiments")
    op.drop_index("ix_experiments_env", table_name="experiments")
    op.drop_table("experiments")
    op.drop_index("ix_code_changes_repo_status", table_name="code_changes")
    op.drop_index("ix_code_changes_pr", table_name="code_changes")
    op.drop_index("ix_code_changes_flag", table_name="code_changes")
    op.drop_table("code_changes")
    op.drop_index("ix_flag_health_score", table_name="feature_flags")
    op.drop_table("feature_flags")
    op.drop_index("ix_agent_tasks_status", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_session_created", table_name="agent_tasks")
    op.drop_table("agent_tasks")
    op.drop_index("ix_agent_sessions_user_updated", table_name="agent_sessions")
    op.drop_index("ix_agent_sessions_thread_id", table_name="agent_sessions")
    op.drop_table("agent_sessions")
    op.drop_index("ix_pr_risk_level", table_name="pull_requests")
    op.drop_index("ix_pr_analysis_status", table_name="pull_requests")
    op.drop_table("pull_requests")
    op.drop_index("ix_repositories_owner_name", table_name="repositories")
    op.drop_index("ix_repositories_full_name", table_name="repositories")
    op.drop_table("repositories")
    op.drop_index("ix_integration_provider_status", table_name="integration_connections")
    op.drop_table("integration_connections")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
