"""M4 provider cost quota ops health foundation

Revision ID: 0005_m4_ops_foundation
Revises: 0004_m3_policy_gate_readiness
Create Date: 2026-06-24 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0005_m4_ops_foundation"
down_revision: str | None = "0004_m3_policy_gate_readiness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "provider_registry_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=False),
        sa.Column("provider_name", sa.Text(), nullable=False),
        sa.Column("provider_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("capability_blob", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("policy_fit_blob", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("cost_model_blob", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("quota_model_blob", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("retry_policy_blob", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("provider_type in ('LLM','TTS','MEDIA','IMAGE','VIDEO','STORAGE','ANALYTICS','PLATFORM','AFFILIATE','OTHER')", name="ck_provider_registry_entries_provider_type"),
        sa.CheckConstraint("status in ('ACTIVE','DISABLED','DEPRECATED','EXPERIMENTAL')", name="ck_provider_registry_entries_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_key"),
    )
    op.create_index("ix_provider_registry_entries_provider_type", "provider_registry_entries", ["provider_type"])
    op.create_index("ix_provider_registry_entries_status", "provider_registry_entries", ["status"])
    op.create_index("ix_provider_registry_entries_created_at", "provider_registry_entries", ["created_at"])

    op.create_table(
        "credential_references",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=False),
        sa.Column("credential_key", sa.String(length=160), nullable=False),
        sa.Column("credential_type", sa.String(length=40), nullable=False),
        sa.Column("secret_ref", sa.Text(), nullable=True),
        sa.Column("scope_blob", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("credential_type in ('API_KEY','OAUTH_CLIENT','OAUTH_TOKEN','SERVICE_ACCOUNT','MANUAL','NONE')", name="ck_credential_references_credential_type"),
        sa.CheckConstraint("status in ('CONFIGURED','MISSING','DISABLED','EXPIRED','REVOKED','UNKNOWN')", name="ck_credential_references_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_key", "credential_key"),
    )
    op.create_index("ix_credential_references_provider_key", "credential_references", ["provider_key"])
    op.create_index("ix_credential_references_status", "credential_references", ["status"])
    op.create_index("ix_credential_references_created_at", "credential_references", ["created_at"])

    op.create_table(
        "credential_health_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_reference_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=False),
        sa.Column("health_state", sa.String(length=40), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("health_state in ('HEALTHY','MISSING','EXPIRED','REVOKED','MISCONFIGURED','UNKNOWN')", name="ck_credential_health_snapshots_health_state"),
        sa.ForeignKeyConstraint(["credential_reference_id"], ["credential_references.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_credential_health_snapshots_credential_reference_id", "credential_health_snapshots", ["credential_reference_id"])
    op.create_index("ix_credential_health_snapshots_provider_key", "credential_health_snapshots", ["provider_key"])
    op.create_index("ix_credential_health_snapshots_checked_at", "credential_health_snapshots", ["checked_at"])

    op.create_table(
        "quota_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=False),
        sa.Column("quota_scope_type", sa.String(length=40), nullable=False),
        sa.Column("quota_scope_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("quota_window", sa.String(length=40), nullable=False),
        sa.Column("quota_limit", sa.Numeric(18, 6), nullable=True),
        sa.Column("quota_used", sa.Numeric(18, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("quota_reserved", sa.Numeric(18, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("unit", sa.String(length=40), nullable=False),
        sa.Column("reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("quota_scope_type in ('COMPANY','CHANNEL','PROJECT','GLOBAL')", name="ck_quota_accounts_quota_scope_type"),
        sa.CheckConstraint("quota_window in ('DAILY','WEEKLY','MONTHLY','CUSTOM')", name="ck_quota_accounts_quota_window"),
        sa.CheckConstraint("unit in ('TOKENS','REQUESTS','SECONDS','CREDITS','BYTES','USD','OTHER')", name="ck_quota_accounts_unit"),
        sa.CheckConstraint("status in ('ACTIVE','EXHAUSTED','DISABLED','UNKNOWN')", name="ck_quota_accounts_status"),
        sa.CheckConstraint("quota_limit is null or quota_limit >= 0", name="ck_quota_accounts_quota_limit_nonnegative"),
        sa.CheckConstraint("quota_used >= 0 and quota_reserved >= 0", name="ck_quota_accounts_amounts_nonnegative"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quota_accounts_provider_scope", "quota_accounts", ["provider_key", "quota_scope_type", "quota_scope_id"])
    op.create_index("ix_quota_accounts_status", "quota_accounts", ["status"])
    op.create_index("ix_quota_accounts_created_at", "quota_accounts", ["created_at"])

    op.create_table(
        "quota_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quota_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_key", sa.String(length=160), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("amount", sa.Numeric(18, 6), nullable=False),
        sa.Column("unit", sa.String(length=40), nullable=False),
        sa.Column("target_type", sa.String(length=80), nullable=True),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason_code", sa.String(length=160), nullable=True),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("event_type in ('RESERVE','CONSUME','RELEASE','ADJUST','RESET','REJECT')", name="ck_quota_events_event_type"),
        sa.CheckConstraint("unit in ('TOKENS','REQUESTS','SECONDS','CREDITS','BYTES','USD','OTHER')", name="ck_quota_events_unit"),
        sa.CheckConstraint("amount >= 0", name="ck_quota_events_amount_nonnegative"),
        sa.ForeignKeyConstraint(["quota_account_id"], ["quota_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quota_events_quota_account_id", "quota_events", ["quota_account_id"])
    op.create_index("ix_quota_events_provider_key", "quota_events", ["provider_key"])
    op.create_index("ix_quota_events_target", "quota_events", ["target_type", "target_id"])
    op.create_index("ix_quota_events_created_at", "quota_events", ["created_at"])

    op.create_table(
        "cost_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=False),
        sa.Column("cost_scope_type", sa.String(length=40), nullable=False),
        sa.Column("cost_scope_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("amount", sa.Numeric(18, 6), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="USD", nullable=False),
        sa.Column("cost_type", sa.String(length=40), nullable=False),
        sa.Column("unit_count", sa.Numeric(18, 6), nullable=True),
        sa.Column("unit_type", sa.String(length=40), nullable=True),
        sa.Column("provider_run_ref", sa.Text(), nullable=True),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("cost_scope_type in ('COMPANY','CHANNEL','PROJECT','ARTIFACT','GATE_RUN','PROVIDER_TEST','GLOBAL')", name="ck_cost_events_cost_scope_type"),
        sa.CheckConstraint("cost_type in ('ESTIMATED','RESERVED','ACTUAL','ADJUSTED','REFUNDED')", name="ck_cost_events_cost_type"),
        sa.CheckConstraint("amount >= 0", name="ck_cost_events_amount_nonnegative"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cost_events_provider_key", "cost_events", ["provider_key"])
    op.create_index("ix_cost_events_scope", "cost_events", ["cost_scope_type", "cost_scope_id"])
    op.create_index("ix_cost_events_created_at", "cost_events", ["created_at"])

    op.create_table(
        "budget_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_key", sa.String(length=160), nullable=False),
        sa.Column("scope_type", sa.String(length=40), nullable=False),
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_blob", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("scope_type in ('COMPANY','CHANNEL','PROJECT','GLOBAL')", name="ck_budget_policies_scope_type"),
        sa.CheckConstraint("status in ('ACTIVE','DISABLED','DRAFT')", name="ck_budget_policies_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("policy_key"),
    )
    op.create_index("ix_budget_policies_scope", "budget_policies", ["scope_type", "scope_id"])
    op.create_index("ix_budget_policies_status", "budget_policies", ["status"])
    op.create_index("ix_budget_policies_created_at", "budget_policies", ["created_at"])

    op.create_table(
        "provider_health_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=False),
        sa.Column("provider_type", sa.String(length=40), nullable=False),
        sa.Column("health_state", sa.String(length=40), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_rate", sa.Numeric(8, 6), nullable=True),
        sa.Column("quota_state", sa.String(length=40), nullable=True),
        sa.Column("reason_codes", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("provider_type in ('LLM','TTS','MEDIA','IMAGE','VIDEO','STORAGE','ANALYTICS','PLATFORM','AFFILIATE','OTHER')", name="ck_provider_health_snapshots_provider_type"),
        sa.CheckConstraint("health_state in ('HEALTHY','DEGRADED','UNAVAILABLE','RATE_LIMITED','QUOTA_EXHAUSTED','CREDENTIAL_MISSING','UNKNOWN')", name="ck_provider_health_snapshots_health_state"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_provider_health_snapshots_provider_key", "provider_health_snapshots", ["provider_key"])
    op.create_index("ix_provider_health_snapshots_health_state", "provider_health_snapshots", ["health_state"])
    op.create_index("ix_provider_health_snapshots_checked_at", "provider_health_snapshots", ["checked_at"])

    op.create_table(
        "component_health_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("component_type", sa.String(length=40), nullable=False),
        sa.Column("component_key", sa.String(length=160), nullable=False),
        sa.Column("health_state", sa.String(length=40), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("component_type in ('PROVIDER','CREDENTIAL','QUOTA','DATABASE','QUEUE','STORAGE','API','CLI','CONFIG','OTHER')", name="ck_component_health_snapshots_component_type"),
        sa.CheckConstraint("health_state in ('HEALTHY','DEGRADED','UNAVAILABLE','UNKNOWN')", name="ck_component_health_snapshots_health_state"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_component_health_snapshots_component", "component_health_snapshots", ["component_type", "component_key"])
    op.create_index("ix_component_health_snapshots_health_state", "component_health_snapshots", ["health_state"])
    op.create_index("ix_component_health_snapshots_checked_at", "component_health_snapshots", ["checked_at"])

    op.create_table(
        "system_health_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("overall_state", sa.String(length=40), nullable=False),
        sa.Column("component_counts", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("active_incident_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("action_required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("overall_state in ('HEALTHY','DEGRADED','BLOCKED','UNKNOWN')", name="ck_system_health_snapshots_overall_state"),
        sa.CheckConstraint("(overall_state in ('HEALTHY','UNKNOWN')) or next_action is not null", name="ck_system_health_snapshots_next_action_required"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_system_health_snapshots_overall_state", "system_health_snapshots", ["overall_state"])
    op.create_index("ix_system_health_snapshots_captured_at", "system_health_snapshots", ["captured_at"])

    op.create_table(
        "retry_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_key", sa.String(length=160), nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=True),
        sa.Column("target_type", sa.String(length=80), nullable=True),
        sa.Column("policy_blob", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status in ('ACTIVE','DISABLED','DRAFT')", name="ck_retry_policies_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("policy_key"),
    )
    op.create_index("ix_retry_policies_provider_key", "retry_policies", ["provider_key"])
    op.create_index("ix_retry_policies_status", "retry_policies", ["status"])
    op.create_index("ix_retry_policies_created_at", "retry_policies", ["created_at"])

    op.create_table(
        "provider_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=False),
        sa.Column("operation_key", sa.String(length=160), nullable=False),
        sa.Column("target_type", sa.String(length=80), nullable=True),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("error_code", sa.String(length=160), nullable=True),
        sa.Column("error_message_redacted", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("cost_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("quota_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.CheckConstraint("status in ('SUCCESS','RETRYABLE_FAILURE','NON_RETRYABLE_FAILURE','QUOTA_REJECTED','CIRCUIT_OPEN','CANCELLED')", name="ck_provider_attempts_status"),
        sa.CheckConstraint("attempt_number > 0", name="ck_provider_attempts_attempt_number_positive"),
        sa.ForeignKeyConstraint(["cost_event_id"], ["cost_events.id"]),
        sa.ForeignKeyConstraint(["quota_event_id"], ["quota_events.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_provider_attempts_provider_operation", "provider_attempts", ["provider_key", "operation_key"])
    op.create_index("ix_provider_attempts_target", "provider_attempts", ["target_type", "target_id"])
    op.create_index("ix_provider_attempts_status", "provider_attempts", ["status"])
    op.create_index("ix_provider_attempts_started_at", "provider_attempts", ["started_at"])

    op.create_table(
        "dead_letter_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("queue_name", sa.String(length=160), nullable=False),
        sa.Column("job_type", sa.String(length=160), nullable=False),
        sa.Column("payload_ref", sa.Text(), nullable=True),
        sa.Column("target_type", sa.String(length=80), nullable=True),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("fail_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("first_failed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("replay_state", sa.String(length=40), nullable=False),
        sa.Column("reason_code", sa.String(length=160), nullable=True),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("replay_state in ('NOT_REPLAYABLE','REPLAYABLE','REPLAYED','DISCARDED')", name="ck_dead_letter_jobs_replay_state"),
        sa.CheckConstraint("fail_count >= 0", name="ck_dead_letter_jobs_fail_count_nonnegative"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dead_letter_jobs_queue_name", "dead_letter_jobs", ["queue_name"])
    op.create_index("ix_dead_letter_jobs_replay_state", "dead_letter_jobs", ["replay_state"])
    op.create_index("ix_dead_letter_jobs_target", "dead_letter_jobs", ["target_type", "target_id"])
    op.create_index("ix_dead_letter_jobs_created_at", "dead_letter_jobs", ["created_at"])

    op.create_table(
        "ops_incidents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("incident_type", sa.String(length=60), nullable=False),
        sa.Column("severity", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("impacted_refs", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("incident_type in ('PROVIDER_OUTAGE','CREDENTIAL_MISSING','QUOTA_EXHAUSTED','COST_LIMIT_REACHED','DEAD_LETTER_JOB','HEALTH_DEGRADED','CONFIG_ERROR','UNKNOWN')", name="ck_ops_incidents_incident_type"),
        sa.CheckConstraint("severity in ('INFO','WARNING','ERROR','CRITICAL')", name="ck_ops_incidents_severity"),
        sa.CheckConstraint("state in ('OPEN','ACKNOWLEDGED','RESOLVED','DISMISSED')", name="ck_ops_incidents_state"),
        sa.CheckConstraint("length(next_action) > 0", name="ck_ops_incidents_next_action_required"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ops_incidents_type", "ops_incidents", ["incident_type"])
    op.create_index("ix_ops_incidents_state", "ops_incidents", ["state"])
    op.create_index("ix_ops_incidents_severity", "ops_incidents", ["severity"])
    op.create_index("ix_ops_incidents_created_at", "ops_incidents", ["created_at"])

    op.create_table(
        "manual_action_queue",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.String(length=60), nullable=False),
        sa.Column("target_type", sa.String(length=80), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("priority", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("reason_code", sa.String(length=160), nullable=True),
        sa.Column("next_action", sa.Text(), nullable=False),
        sa.Column("assignee_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("action_type in ('CHECK_CREDENTIAL','UPDATE_CREDENTIAL_REF','REVIEW_COST_LIMIT','REVIEW_QUOTA','INVESTIGATE_PROVIDER','REPLAY_DEAD_LETTER','RESOLVE_INCIDENT','OTHER')", name="ck_manual_action_queue_action_type"),
        sa.CheckConstraint("priority in ('LOW','MEDIUM','HIGH','URGENT')", name="ck_manual_action_queue_priority"),
        sa.CheckConstraint("state in ('OPEN','IN_PROGRESS','DONE','CANCELLED')", name="ck_manual_action_queue_state"),
        sa.CheckConstraint("length(next_action) > 0", name="ck_manual_action_queue_next_action_required"),
        sa.ForeignKeyConstraint(["assignee_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_manual_action_queue_action_type", "manual_action_queue", ["action_type"])
    op.create_index("ix_manual_action_queue_state", "manual_action_queue", ["state"])
    op.create_index("ix_manual_action_queue_priority", "manual_action_queue", ["priority"])
    op.create_index("ix_manual_action_queue_target", "manual_action_queue", ["target_type", "target_id"])
    op.create_index("ix_manual_action_queue_created_at", "manual_action_queue", ["created_at"])

    op.add_column("llm_run_snapshots", sa.Column("provider_key", sa.String(length=160), nullable=True))
    op.add_column("llm_run_snapshots", sa.Column("model_key", sa.String(length=160), nullable=True))
    op.add_column("llm_run_snapshots", sa.Column("run_mode", sa.String(length=40), nullable=True))
    op.add_column("llm_run_snapshots", sa.Column("estimated_cost", sa.Numeric(18, 6), nullable=True))
    op.add_column("llm_run_snapshots", sa.Column("token_estimate", sa.Numeric(18, 6), nullable=True))
    op.add_column("llm_run_snapshots", sa.Column("quota_event_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("llm_run_snapshots", sa.Column("cost_event_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_check_constraint(
        "ck_llm_run_snapshots_run_mode",
        "llm_run_snapshots",
        "run_mode is null or run_mode in ('MOCK','REAL_DISABLED','REAL')",
    )
    op.create_foreign_key("fk_llm_run_snapshots_quota_event_id_quota_events", "llm_run_snapshots", "quota_events", ["quota_event_id"], ["id"])
    op.create_foreign_key("fk_llm_run_snapshots_cost_event_id_cost_events", "llm_run_snapshots", "cost_events", ["cost_event_id"], ["id"])
    op.create_index("ix_llm_run_snapshots_provider_key", "llm_run_snapshots", ["provider_key"])
    op.create_index("ix_llm_run_snapshots_run_mode", "llm_run_snapshots", ["run_mode"])

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_m4_append_only_change()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% rows are append-only', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in [
        "credential_health_snapshots",
        "quota_events",
        "cost_events",
        "provider_health_snapshots",
        "component_health_snapshots",
        "system_health_snapshots",
        "provider_attempts",
    ]:
        op.execute(
            f"""
            CREATE TRIGGER trg_prevent_{table}_change
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION prevent_m4_append_only_change();
            """
        )


def downgrade() -> None:
    for table in [
        "provider_attempts",
        "system_health_snapshots",
        "component_health_snapshots",
        "provider_health_snapshots",
        "cost_events",
        "quota_events",
        "credential_health_snapshots",
    ]:
        op.execute(f"DROP TRIGGER IF EXISTS trg_prevent_{table}_change ON {table}")
    op.execute("DROP FUNCTION IF EXISTS prevent_m4_append_only_change()")

    op.drop_index("ix_llm_run_snapshots_run_mode", table_name="llm_run_snapshots")
    op.drop_index("ix_llm_run_snapshots_provider_key", table_name="llm_run_snapshots")
    op.drop_constraint("fk_llm_run_snapshots_cost_event_id_cost_events", "llm_run_snapshots", type_="foreignkey")
    op.drop_constraint("fk_llm_run_snapshots_quota_event_id_quota_events", "llm_run_snapshots", type_="foreignkey")
    op.drop_constraint("ck_llm_run_snapshots_run_mode", "llm_run_snapshots", type_="check")
    op.drop_column("llm_run_snapshots", "cost_event_id")
    op.drop_column("llm_run_snapshots", "quota_event_id")
    op.drop_column("llm_run_snapshots", "token_estimate")
    op.drop_column("llm_run_snapshots", "estimated_cost")
    op.drop_column("llm_run_snapshots", "run_mode")
    op.drop_column("llm_run_snapshots", "model_key")
    op.drop_column("llm_run_snapshots", "provider_key")

    op.drop_index("ix_manual_action_queue_created_at", table_name="manual_action_queue")
    op.drop_index("ix_manual_action_queue_target", table_name="manual_action_queue")
    op.drop_index("ix_manual_action_queue_priority", table_name="manual_action_queue")
    op.drop_index("ix_manual_action_queue_state", table_name="manual_action_queue")
    op.drop_index("ix_manual_action_queue_action_type", table_name="manual_action_queue")
    op.drop_table("manual_action_queue")
    op.drop_index("ix_ops_incidents_created_at", table_name="ops_incidents")
    op.drop_index("ix_ops_incidents_severity", table_name="ops_incidents")
    op.drop_index("ix_ops_incidents_state", table_name="ops_incidents")
    op.drop_index("ix_ops_incidents_type", table_name="ops_incidents")
    op.drop_table("ops_incidents")
    op.drop_index("ix_dead_letter_jobs_created_at", table_name="dead_letter_jobs")
    op.drop_index("ix_dead_letter_jobs_target", table_name="dead_letter_jobs")
    op.drop_index("ix_dead_letter_jobs_replay_state", table_name="dead_letter_jobs")
    op.drop_index("ix_dead_letter_jobs_queue_name", table_name="dead_letter_jobs")
    op.drop_table("dead_letter_jobs")
    op.drop_index("ix_provider_attempts_started_at", table_name="provider_attempts")
    op.drop_index("ix_provider_attempts_status", table_name="provider_attempts")
    op.drop_index("ix_provider_attempts_target", table_name="provider_attempts")
    op.drop_index("ix_provider_attempts_provider_operation", table_name="provider_attempts")
    op.drop_table("provider_attempts")
    op.drop_index("ix_retry_policies_created_at", table_name="retry_policies")
    op.drop_index("ix_retry_policies_status", table_name="retry_policies")
    op.drop_index("ix_retry_policies_provider_key", table_name="retry_policies")
    op.drop_table("retry_policies")
    op.drop_index("ix_system_health_snapshots_captured_at", table_name="system_health_snapshots")
    op.drop_index("ix_system_health_snapshots_overall_state", table_name="system_health_snapshots")
    op.drop_table("system_health_snapshots")
    op.drop_index("ix_component_health_snapshots_checked_at", table_name="component_health_snapshots")
    op.drop_index("ix_component_health_snapshots_health_state", table_name="component_health_snapshots")
    op.drop_index("ix_component_health_snapshots_component", table_name="component_health_snapshots")
    op.drop_table("component_health_snapshots")
    op.drop_index("ix_provider_health_snapshots_checked_at", table_name="provider_health_snapshots")
    op.drop_index("ix_provider_health_snapshots_health_state", table_name="provider_health_snapshots")
    op.drop_index("ix_provider_health_snapshots_provider_key", table_name="provider_health_snapshots")
    op.drop_table("provider_health_snapshots")
    op.drop_index("ix_budget_policies_created_at", table_name="budget_policies")
    op.drop_index("ix_budget_policies_status", table_name="budget_policies")
    op.drop_index("ix_budget_policies_scope", table_name="budget_policies")
    op.drop_table("budget_policies")
    op.drop_index("ix_cost_events_created_at", table_name="cost_events")
    op.drop_index("ix_cost_events_scope", table_name="cost_events")
    op.drop_index("ix_cost_events_provider_key", table_name="cost_events")
    op.drop_table("cost_events")
    op.drop_index("ix_quota_events_created_at", table_name="quota_events")
    op.drop_index("ix_quota_events_target", table_name="quota_events")
    op.drop_index("ix_quota_events_provider_key", table_name="quota_events")
    op.drop_index("ix_quota_events_quota_account_id", table_name="quota_events")
    op.drop_table("quota_events")
    op.drop_index("ix_quota_accounts_created_at", table_name="quota_accounts")
    op.drop_index("ix_quota_accounts_status", table_name="quota_accounts")
    op.drop_index("ix_quota_accounts_provider_scope", table_name="quota_accounts")
    op.drop_table("quota_accounts")
    op.drop_index("ix_credential_health_snapshots_checked_at", table_name="credential_health_snapshots")
    op.drop_index("ix_credential_health_snapshots_provider_key", table_name="credential_health_snapshots")
    op.drop_index("ix_credential_health_snapshots_credential_reference_id", table_name="credential_health_snapshots")
    op.drop_table("credential_health_snapshots")
    op.drop_index("ix_credential_references_created_at", table_name="credential_references")
    op.drop_index("ix_credential_references_status", table_name="credential_references")
    op.drop_index("ix_credential_references_provider_key", table_name="credential_references")
    op.drop_table("credential_references")
    op.drop_index("ix_provider_registry_entries_created_at", table_name="provider_registry_entries")
    op.drop_index("ix_provider_registry_entries_status", table_name="provider_registry_entries")
    op.drop_index("ix_provider_registry_entries_provider_type", table_name="provider_registry_entries")
    op.drop_table("provider_registry_entries")
