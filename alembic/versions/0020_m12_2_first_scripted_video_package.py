"""M12.2 first scripted video package activation

Revision ID: 0020_m12_2_video_package
Revises: 0019_m12_1_prompt_registry
Create Date: 2026-06-28 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0020_m12_2_video_package"
down_revision: str | None = "0019_m12_1_prompt_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
STATUS_CHECK = "package_status in ('READY_FOR_HUMAN_REVIEW','REVIEW_REQUIRED','BLOCKED','NOT_CONFIGURED','ERROR')"


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "first_scripted_video_packages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_profile_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("compiled_policy_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_readiness_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("package_status", sa.String(length=40), nullable=False),
        sa.Column("agent_run_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("prompt_render_run_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("prompt_audit_snapshot_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("artifacts", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("limitations", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("risk_limitations_summary", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(STATUS_CHECK, name="ck_first_scripted_video_packages_status"),
        sa.CheckConstraint("jsonb_typeof(agent_run_refs) = 'array'", name="ck_first_scripted_video_packages_agent_refs_array"),
        sa.CheckConstraint("jsonb_typeof(prompt_render_run_refs) = 'array'", name="ck_first_scripted_video_packages_render_refs_array"),
        sa.CheckConstraint("jsonb_typeof(prompt_audit_snapshot_refs) = 'array'", name="ck_first_scripted_video_packages_audit_refs_array"),
        sa.CheckConstraint("jsonb_typeof(artifacts) = 'object'", name="ck_first_scripted_video_packages_artifacts_object"),
        sa.CheckConstraint("jsonb_typeof(limitations) = 'array'", name="ck_first_scripted_video_packages_limitations_array"),
        sa.CheckConstraint("jsonb_typeof(risk_limitations_summary) = 'object'", name="ck_first_scripted_video_packages_risk_object"),
        sa.ForeignKeyConstraint(["channel_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["channel_profile_version_id"], ["channel_profile_versions.id"]),
        sa.ForeignKeyConstraint(["compiled_policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.ForeignKeyConstraint(["provider_readiness_snapshot_id"], ["provider_readiness_snapshots.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_first_scripted_video_packages_channel", "first_scripted_video_packages", ["channel_id"])
    op.create_index("ix_first_scripted_video_packages_project", "first_scripted_video_packages", ["video_project_id"])
    op.create_index("ix_first_scripted_video_packages_status", "first_scripted_video_packages", ["package_status"])
    op.create_index("ix_first_scripted_video_packages_created_at", "first_scripted_video_packages", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_first_scripted_video_packages_created_at", table_name="first_scripted_video_packages")
    op.drop_index("ix_first_scripted_video_packages_status", table_name="first_scripted_video_packages")
    op.drop_index("ix_first_scripted_video_packages_project", table_name="first_scripted_video_packages")
    op.drop_index("ix_first_scripted_video_packages_channel", table_name="first_scripted_video_packages")
    op.drop_table("first_scripted_video_packages")
