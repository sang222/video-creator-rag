"""Add durable long-form-only analytics scheduler authority.

Revision ID: 0050_vcos_long_form_analytics
Revises: 0049_vcos_long_form_cadence
Create Date: 2026-08-01 09:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0050_vcos_long_form_analytics"
down_revision: str | None = "0049_vcos_long_form_cadence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
LONG_FORM_OBSERVATION_WINDOWS = "'H24','H72','D7','D30'"
_M9_OBSERVATION_TABLES = (
    "post_publish_observation_windows",
    "post_publish_health_runs",
    "no_view_diagnostic_runs",
    "packaging_diagnostic_runs",
    "retention_diagnostic_runs",
    "engagement_diagnostic_runs",
    "policy_rights_diagnostic_runs",
    "failure_trace_reports",
)


def _replace_observation_window_constraint(*, include_long_form: bool) -> None:
    """Rebuild historic guards using server-resolved constraint names.

    The project's SQLAlchemy naming convention prefixes legacy explicit names,
    and PostgreSQL then truncates them. Resolving the live name keeps this
    migration valid for both fresh and already-migrated databases.
    """

    bind = op.get_bind()
    tables = ("analytics_snapshots", *_M9_OBSERVATION_TABLES)
    for table_name in tables:
        names = bind.execute(
            sa.text(
                "select conname from pg_constraint "
                "where conrelid = to_regclass(:table_name) "
                "and contype = 'c' "
                "and pg_get_constraintdef(oid) like '%observation_window%'"
            ),
            {"table_name": table_name},
        ).scalars()
        for constraint_name in names:
            op.execute(
                f'ALTER TABLE "{table_name}" DROP CONSTRAINT "{constraint_name}"'
            )
        legacy = "'T_PLUS_1H','T_PLUS_6H','T_PLUS_24H','T_PLUS_48H','T_PLUS_7D','CUSTOM'"
        values = (
            f"{legacy},{LONG_FORM_OBSERVATION_WINDOWS}"
            if include_long_form
            else legacy
        )
        if table_name == "analytics_snapshots":
            values = f"{values},'UNKNOWN'"
        op.create_check_constraint(
            f"vcos_lfa_{table_name}_observation_window",
            table_name,
            f"observation_window in ({values})",
        )


def _assert_no_long_form_observation_data() -> None:
    bind = op.get_bind()
    for table_name in ("analytics_snapshots", *_M9_OBSERVATION_TABLES):
        count = bind.execute(
            sa.text(
                f"select count(*) from \"{table_name}\" "
                "where observation_window in ('H24','H72','D7','D30')"
            )
        ).scalar_one()
        if count:
            raise RuntimeError("DOWNGRADE_BLOCKED_LONG_FORM_ANALYTICS_OBSERVATIONS_EXIST")


def upgrade() -> None:
    _replace_observation_window_constraint(include_long_form=True)
    op.create_table(
        "long_form_analytics_windows",
        sa.Column("id", UUID, nullable=False),
        sa.Column("uploaded_video_id", UUID, nullable=False),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("video_project_id", UUID, nullable=False),
        sa.Column("policy_snapshot_id", UUID, nullable=False),
        sa.Column("channel_profile_version_id", UUID, nullable=False),
        sa.Column("destination_binding_id", UUID, nullable=False),
        sa.Column("destination_binding_fingerprint", sa.String(64), nullable=False),
        sa.Column("target_market_lineage", JSONB, nullable=False),
        sa.Column("production_lane", sa.String(40), nullable=False),
        sa.Column("content_mode", sa.String(40), nullable=False),
        sa.Column("series_plan_id", UUID, nullable=True),
        sa.Column("series_run_id", UUID, nullable=True),
        sa.Column("episode_number", sa.Integer(), nullable=True),
        sa.Column("standalone_reason_code", sa.String(160), nullable=True),
        sa.Column("metric_authority", sa.String(40), nullable=False),
        sa.Column("window_type", sa.String(16), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("minimum_maturity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(160), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("analytics_snapshot_id", UUID, nullable=True),
        sa.Column("post_publish_health_run_id", UUID, nullable=True),
        sa.Column("canonical_input_hash", sa.String(64), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=True),
        sa.Column("reason_codes", JSONB, nullable=False),
        sa.Column("metadata", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("production_lane = 'LONG_FORM'", name="ck_lfa_windows_long_form_only"),
        sa.CheckConstraint("window_type in ('H24','H72','D7','D30')", name="ck_lfa_windows_type"),
        sa.CheckConstraint("metric_authority in ('YOUTUBE_OWNER','YOUTUBE_PUBLIC','MANUAL_VERIFIED')", name="ck_lfa_windows_authority"),
        sa.CheckConstraint("state in ('SCHEDULED','WAITING_FOR_MATURITY','READY_TO_SYNC','SYNCING','SYNCED','DIAGNOSTICS_PENDING','DIAGNOSTICS_COMPLETE','RETRY_SCHEDULED','BLOCKED_AUTH','BLOCKED_DATA_UNAVAILABLE','FAILED_TERMINAL','CANCELED')", name="ck_lfa_windows_state"),
        sa.CheckConstraint("attempt_count >= 0 and max_attempts > 0 and attempt_count <= max_attempts", name="ck_lfa_windows_attempts"),
        sa.CheckConstraint("(content_mode = 'SERIES_EPISODE' and series_plan_id is not null and series_run_id is not null and episode_number > 0 and standalone_reason_code is null) or (content_mode = 'STANDALONE' and series_plan_id is null and series_run_id is null and episode_number is null and standalone_reason_code is not null)", name="ck_lfa_windows_assignment"),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.ForeignKeyConstraint(["channel_profile_version_id"], ["channel_profile_versions.id"]),
        sa.ForeignKeyConstraint(["series_plan_id"], ["series_plans.id"]),
        sa.ForeignKeyConstraint(["series_run_id"], ["series_runs.id"]),
        sa.ForeignKeyConstraint(["analytics_snapshot_id"], ["analytics_snapshots.id"]),
        sa.ForeignKeyConstraint(["post_publish_health_run_id"], ["post_publish_health_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uploaded_video_id", "metric_authority", "window_type", name="uq_long_form_analytics_window_authority"),
        sa.UniqueConstraint("canonical_input_hash", name="uq_long_form_analytics_window_input_hash"),
    )
    op.create_index("ix_long_form_analytics_windows_due", "long_form_analytics_windows", ["state", "scheduled_for", "next_attempt_at"])
    op.create_index("ix_long_form_analytics_windows_uploaded", "long_form_analytics_windows", ["uploaded_video_id"])
    op.create_index("ix_long_form_analytics_windows_channel", "long_form_analytics_windows", ["channel_workspace_id"])
    op.add_column(
        "analytics_snapshots",
        sa.Column("long_form_analytics_window_id", UUID, nullable=True),
    )
    op.create_foreign_key(
        "fk_analytics_snapshots_long_form_window",
        "analytics_snapshots",
        "long_form_analytics_windows",
        ["long_form_analytics_window_id"],
        ["id"],
    )
    op.create_index(
        "uq_analytics_snapshots_long_form_window",
        "analytics_snapshots",
        ["long_form_analytics_window_id"],
        unique=True,
        postgresql_where=sa.text("long_form_analytics_window_id is not null"),
    )


def downgrade() -> None:
    count = op.get_bind().execute(sa.text("select count(*) from long_form_analytics_windows")).scalar_one()
    if count:
        raise RuntimeError("DOWNGRADE_BLOCKED_LONG_FORM_ANALYTICS_AUTHORITY_EXISTS")
    _assert_no_long_form_observation_data()
    op.execute("DROP INDEX IF EXISTS uq_analytics_snapshots_long_form_window")
    op.execute(
        "ALTER TABLE analytics_snapshots "
        "DROP CONSTRAINT IF EXISTS fk_analytics_snapshots_long_form_window"
    )
    op.execute(
        "ALTER TABLE analytics_snapshots "
        "DROP COLUMN IF EXISTS long_form_analytics_window_id"
    )
    op.drop_index("ix_long_form_analytics_windows_channel", table_name="long_form_analytics_windows")
    op.drop_index("ix_long_form_analytics_windows_uploaded", table_name="long_form_analytics_windows")
    op.drop_index("ix_long_form_analytics_windows_due", table_name="long_form_analytics_windows")
    op.drop_table("long_form_analytics_windows")
    _replace_observation_window_constraint(include_long_form=False)
