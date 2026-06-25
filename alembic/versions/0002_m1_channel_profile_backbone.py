"""M1 channel profile backbone

Revision ID: 0002_m1_channel_profile_backbone
Revises: 0001_m0_foundation
Create Date: 2026-06-23 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_m1_channel_profile_backbone"
down_revision: str | None = "0001_m0_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "channel_workspaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("primary_language", sa.Text(), nullable=False),
        sa.Column("target_market", sa.Text(), nullable=True),
        sa.Column("default_timezone", sa.Text(), nullable=False),
        sa.Column("active_policy_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "key"),
    )
    op.create_index("ix_channel_workspaces_company_id", "channel_workspaces", ["company_id"])
    op.create_index("ix_channel_workspaces_created_at", "channel_workspaces", ["created_at"])

    op.create_table(
        "channel_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_workspace_id", "user_id", "role_id"),
    )
    op.create_index("ix_channel_memberships_channel_workspace_id", "channel_memberships", ["channel_workspace_id"])
    op.create_index("ix_channel_memberships_user_id", "channel_memberships", ["user_id"])

    op.create_table(
        "channel_profile_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("profile_input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("profile_input_hash", sa.Text(), nullable=False),
        sa.Column("source_template_key", sa.Text(), nullable=True),
        sa.Column("source_template_version", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_workspace_id", "version"),
    )
    op.create_index("ix_channel_profile_versions_channel_workspace_id", "channel_profile_versions", ["channel_workspace_id"])
    op.create_index("ix_channel_profile_versions_created_at", "channel_profile_versions", ["created_at"])

    op.create_table(
        "channel_profile_compile_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_profile_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("compiler_version", sa.Text(), nullable=False),
        sa.Column("capability_matrix_version", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column("output_hash", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("diagnostics", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["channel_profile_version_id"], ["channel_profile_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_channel_profile_compile_runs_channel_profile_version_id", "channel_profile_compile_runs", ["channel_profile_version_id"])
    op.create_index("ix_channel_profile_compile_runs_correlation_id", "channel_profile_compile_runs", ["correlation_id"])
    op.create_index("ix_channel_profile_compile_runs_created_at", "channel_profile_compile_runs", ["created_at"])

    op.create_table(
        "compiled_channel_policy_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_profile_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("compile_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("snapshot_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("compiler_version", sa.Text(), nullable=False),
        sa.Column("capability_matrix_version", sa.Text(), nullable=False),
        sa.Column("compiled_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("profile_input_hash", sa.Text(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["channel_profile_version_id"], ["channel_profile_versions.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["compile_run_id"], ["channel_profile_compile_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_workspace_id", "snapshot_version"),
        sa.UniqueConstraint(
            "channel_profile_version_id",
            "compiler_version",
            "capability_matrix_version",
            "profile_input_hash",
            "content_hash",
            name="uq_policy_snapshot_profile_compile_identity",
        ),
    )
    op.create_index("ix_compiled_channel_policy_snapshots_channel_workspace_id", "compiled_channel_policy_snapshots", ["channel_workspace_id"])
    op.create_index("ix_compiled_channel_policy_snapshots_channel_profile_version_id", "compiled_channel_policy_snapshots", ["channel_profile_version_id"])
    op.create_index("ix_compiled_channel_policy_snapshots_created_at", "compiled_channel_policy_snapshots", ["created_at"])
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_policy_snapshot_immutable_field_update()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.channel_workspace_id IS DISTINCT FROM NEW.channel_workspace_id
                OR OLD.channel_profile_version_id IS DISTINCT FROM NEW.channel_profile_version_id
                OR OLD.compile_run_id IS DISTINCT FROM NEW.compile_run_id
                OR OLD.snapshot_version IS DISTINCT FROM NEW.snapshot_version
                OR OLD.compiler_version IS DISTINCT FROM NEW.compiler_version
                OR OLD.capability_matrix_version IS DISTINCT FROM NEW.capability_matrix_version
                OR OLD.compiled_payload IS DISTINCT FROM NEW.compiled_payload
                OR OLD.content_hash IS DISTINCT FROM NEW.content_hash
                OR OLD.profile_input_hash IS DISTINCT FROM NEW.profile_input_hash
                OR OLD.created_at IS DISTINCT FROM NEW.created_at
            THEN
                RAISE EXCEPTION 'compiled_channel_policy_snapshots immutable fields cannot be changed after creation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_prevent_policy_snapshot_immutable_field_update
        BEFORE UPDATE ON compiled_channel_policy_snapshots
        FOR EACH ROW EXECUTE FUNCTION prevent_policy_snapshot_immutable_field_update();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_prevent_policy_snapshot_immutable_field_update ON compiled_channel_policy_snapshots")
    op.execute("DROP FUNCTION IF EXISTS prevent_policy_snapshot_immutable_field_update()")
    op.drop_index("ix_compiled_channel_policy_snapshots_created_at", table_name="compiled_channel_policy_snapshots")
    op.drop_index("ix_compiled_channel_policy_snapshots_channel_profile_version_id", table_name="compiled_channel_policy_snapshots")
    op.drop_index("ix_compiled_channel_policy_snapshots_channel_workspace_id", table_name="compiled_channel_policy_snapshots")
    op.drop_table("compiled_channel_policy_snapshots")
    op.drop_index("ix_channel_profile_compile_runs_created_at", table_name="channel_profile_compile_runs")
    op.drop_index("ix_channel_profile_compile_runs_correlation_id", table_name="channel_profile_compile_runs")
    op.drop_index("ix_channel_profile_compile_runs_channel_profile_version_id", table_name="channel_profile_compile_runs")
    op.drop_table("channel_profile_compile_runs")
    op.drop_index("ix_channel_profile_versions_created_at", table_name="channel_profile_versions")
    op.drop_index("ix_channel_profile_versions_channel_workspace_id", table_name="channel_profile_versions")
    op.drop_table("channel_profile_versions")
    op.drop_index("ix_channel_memberships_user_id", table_name="channel_memberships")
    op.drop_index("ix_channel_memberships_channel_workspace_id", table_name="channel_memberships")
    op.drop_table("channel_memberships")
    op.drop_index("ix_channel_workspaces_created_at", table_name="channel_workspaces")
    op.drop_index("ix_channel_workspaces_company_id", table_name="channel_workspaces")
    op.drop_table("channel_workspaces")
