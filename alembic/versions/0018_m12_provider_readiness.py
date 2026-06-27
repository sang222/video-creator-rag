"""M12 provider readiness and guarded smoke

Revision ID: 0018_m12_provider_readiness
Revises: 0017_m11_1_localization
Create Date: 2026-06-27 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0018_m12_provider_readiness"
down_revision: str | None = "0017_m11_1_localization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())

CHECK_TYPE = "check_type in ('CONFIG','CREDENTIAL','CONNECTION','REAL_SMOKE','CAPABILITY','BUDGET','SECURITY')"
CHECK_STATE = "check_state in ('PASS','WARNING','BLOCKED','SKIPPED','FAILED','UNKNOWN')"
SNAPSHOT_STATE = "snapshot_state in ('READY','PARTIAL','BLOCKED','UNKNOWN')"
SMOKE_STATE = "run_state in ('SKIPPED','RUNNING','PASS','FAILED','BLOCKED')"


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def _uuid(name: str) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), nullable=False)


def upgrade() -> None:
    op.create_table(
        "provider_readiness_checks",
        _uuid("id"),
        sa.Column("provider_key", sa.String(length=160), nullable=False),
        sa.Column("provider_type", sa.String(length=80), nullable=False),
        sa.Column("check_type", sa.String(length=40), nullable=False),
        sa.Column("check_state", sa.String(length=40), nullable=False),
        sa.Column("operator_summary", sa.Text(), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("technical_appendix", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        sa.CheckConstraint(CHECK_TYPE, name="ck_provider_readiness_checks_type"),
        sa.CheckConstraint(CHECK_STATE, name="ck_provider_readiness_checks_state"),
        sa.CheckConstraint("jsonb_typeof(reason_codes) = 'array'", name="ck_provider_readiness_checks_reasons_array"),
        sa.CheckConstraint("jsonb_typeof(technical_appendix) = 'object'", name="ck_provider_readiness_checks_appendix_object"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_provider_readiness_checks_provider", "provider_readiness_checks", ["provider_key"])
    op.create_index("ix_provider_readiness_checks_type_state", "provider_readiness_checks", ["check_type", "check_state"])
    op.create_index("ix_provider_readiness_checks_created_at", "provider_readiness_checks", ["created_at"])

    op.create_table(
        "provider_readiness_snapshots",
        _uuid("id"),
        sa.Column("snapshot_state", sa.String(length=40), nullable=False),
        sa.Column("provider_summaries", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("blocking_items", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("warning_items", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("next_actions", JSONB, server_default=_jsonb_array(), nullable=False),
        _created_at(),
        sa.CheckConstraint(SNAPSHOT_STATE, name="ck_provider_readiness_snapshots_state"),
        sa.CheckConstraint("jsonb_typeof(provider_summaries) = 'array'", name="ck_provider_readiness_snapshots_summaries_array"),
        sa.CheckConstraint("jsonb_typeof(blocking_items) = 'array'", name="ck_provider_readiness_snapshots_blocking_array"),
        sa.CheckConstraint("jsonb_typeof(warning_items) = 'array'", name="ck_provider_readiness_snapshots_warning_array"),
        sa.CheckConstraint("jsonb_typeof(next_actions) = 'array'", name="ck_provider_readiness_snapshots_actions_array"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_provider_readiness_snapshots_state", "provider_readiness_snapshots", ["snapshot_state"])
    op.create_index("ix_provider_readiness_snapshots_created_at", "provider_readiness_snapshots", ["created_at"])

    op.create_table(
        "real_smoke_runs",
        _uuid("id"),
        sa.Column("provider_key", sa.String(length=160), nullable=False),
        sa.Column("smoke_type", sa.String(length=80), nullable=False),
        sa.Column("run_state", sa.String(length=40), nullable=False),
        sa.Column("env_flags", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=160), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("technical_appendix", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        sa.CheckConstraint(SMOKE_STATE, name="ck_real_smoke_runs_state"),
        sa.CheckConstraint("jsonb_typeof(env_flags) = 'object'", name="ck_real_smoke_runs_env_flags_object"),
        sa.CheckConstraint("jsonb_typeof(technical_appendix) = 'object'", name="ck_real_smoke_runs_appendix_object"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_real_smoke_runs_provider", "real_smoke_runs", ["provider_key"])
    op.create_index("ix_real_smoke_runs_state", "real_smoke_runs", ["run_state"])
    op.create_index("ix_real_smoke_runs_created_at", "real_smoke_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_real_smoke_runs_created_at", table_name="real_smoke_runs")
    op.drop_index("ix_real_smoke_runs_state", table_name="real_smoke_runs")
    op.drop_index("ix_real_smoke_runs_provider", table_name="real_smoke_runs")
    op.drop_table("real_smoke_runs")

    op.drop_index("ix_provider_readiness_snapshots_created_at", table_name="provider_readiness_snapshots")
    op.drop_index("ix_provider_readiness_snapshots_state", table_name="provider_readiness_snapshots")
    op.drop_table("provider_readiness_snapshots")

    op.drop_index("ix_provider_readiness_checks_created_at", table_name="provider_readiness_checks")
    op.drop_index("ix_provider_readiness_checks_type_state", table_name="provider_readiness_checks")
    op.drop_index("ix_provider_readiness_checks_provider", table_name="provider_readiness_checks")
    op.drop_table("provider_readiness_checks")
