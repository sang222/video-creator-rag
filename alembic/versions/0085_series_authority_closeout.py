"""Series arc length, completion and public ordinal authority.

Revision ID: 0085_series_authority_closeout
Revises: 0084_youtube_private_delivery
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0085_series_authority_closeout"
down_revision = "0084_youtube_private_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "series_arc_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("channel_workspaces.id"), nullable=False),
        sa.Column("series_plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("series_plans.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("planning_mode", sa.String(24), nullable=False),
        sa.Column("planned_episode_count", sa.Integer()),
        sa.Column("editorial_coverage", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("state", sa.String(24), nullable=False, server_default="DRAFT"),
        sa.Column("supersedes_series_arc_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("series_arc_versions.id")),
        sa.Column("approval_evidence_refs", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("series_plan_id", "version", name="uq_series_arc_versions_plan_version"),
        sa.UniqueConstraint("content_hash", name="uq_series_arc_versions_hash"),
        sa.CheckConstraint("version > 0", name="ck_series_arc_versions_version"),
        sa.CheckConstraint("planning_mode in ('FIXED_COUNT','ROLLING')", name="ck_series_arc_versions_mode"),
        sa.CheckConstraint("(planning_mode = 'FIXED_COUNT' and planned_episode_count > 0) or (planning_mode = 'ROLLING' and planned_episode_count is null)", name="ck_series_arc_versions_count"),
        sa.CheckConstraint("state in ('DRAFT','APPROVED','SUPERSEDED','ARCHIVED')", name="ck_series_arc_versions_state"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_series_arc_versions_hash"),
    )
    op.create_index("ix_series_arc_versions_plan_state", "series_arc_versions", ["series_plan_id", "state"])
    op.create_index("ix_series_arc_versions_channel", "series_arc_versions", ["channel_workspace_id"])
    op.execute("CREATE UNIQUE INDEX uq_series_arc_versions_one_approved ON series_arc_versions(series_plan_id) WHERE state = 'APPROVED'")

    op.create_table(
        "series_episode_blueprints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("series_arc_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("series_arc_versions.id"), nullable=False),
        sa.Column("series_plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("series_plans.id"), nullable=False),
        sa.Column("blueprint_key", sa.String(160), nullable=False),
        sa.Column("editorial_position", sa.Integer(), nullable=False),
        sa.Column("title_hint", sa.Text()),
        sa.Column("editorial_purpose", sa.Text(), nullable=False),
        sa.Column("coverage_contract", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("state", sa.String(24), nullable=False, server_default="PLANNED"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("series_arc_version_id", "blueprint_key", name="uq_series_episode_blueprints_key"),
        sa.UniqueConstraint("series_arc_version_id", "editorial_position", name="uq_series_episode_blueprints_position"),
        sa.CheckConstraint("editorial_position > 0", name="ck_series_episode_blueprints_position"),
        sa.CheckConstraint("state in ('PLANNED','OPTIONAL','DEFERRED')", name="ck_series_episode_blueprints_state"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_series_episode_blueprints_hash"),
    )
    op.create_index("ix_series_episode_blueprints_plan", "series_episode_blueprints", ["series_plan_id"])

    op.create_table(
        "series_episode_attempt_authorities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("series_plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("series_plans.id"), nullable=False),
        sa.Column("series_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("series_runs.id"), nullable=False),
        sa.Column("series_arc_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("series_arc_versions.id"), nullable=False),
        sa.Column("episode_blueprint_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("series_episode_blueprints.id")),
        sa.Column("technical_attempt_number", sa.Integer(), nullable=False),
        sa.Column("reservation_ref", sa.Text(), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("video_projects.id")),
        sa.Column("state", sa.String(24), nullable=False, server_default="RESERVED"),
        sa.Column("identity_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("series_run_id", "technical_attempt_number", name="uq_series_episode_attempts_run_number"),
        sa.UniqueConstraint("identity_hash", name="uq_series_episode_attempts_hash"),
        sa.CheckConstraint("technical_attempt_number > 0", name="ck_series_episode_attempts_number"),
        sa.CheckConstraint("state in ('RESERVED','QUALIFIED','ADMITTED','ABANDONED','PUBLISHED')", name="ck_series_episode_attempts_state"),
        sa.CheckConstraint("identity_hash ~ '^[0-9a-f]{64}$'", name="ck_series_episode_attempts_hash"),
    )
    op.create_index("ix_series_episode_attempts_project", "series_episode_attempt_authorities", ["video_project_id"])
    op.create_index("ix_series_episode_attempts_arc", "series_episode_attempt_authorities", ["series_arc_version_id"])

    op.create_table(
        "series_public_ordinal_authorities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("channel_workspaces.id"), nullable=False),
        sa.Column("series_plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("series_plans.id"), nullable=False),
        sa.Column("series_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("series_runs.id"), nullable=False),
        sa.Column("series_arc_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("series_arc_versions.id"), nullable=False),
        sa.Column("episode_attempt_authority_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("series_episode_attempt_authorities.id"), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("video_projects.id"), nullable=False),
        sa.Column("public_publication_receipt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("public_publication_receipts.id"), nullable=False),
        sa.Column("public_episode_ordinal", sa.Integer(), nullable=False),
        sa.Column("authority_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("series_plan_id", "public_episode_ordinal", name="uq_series_public_ordinals_plan_ordinal"),
        sa.UniqueConstraint("video_project_id", name="uq_series_public_ordinals_project"),
        sa.UniqueConstraint("public_publication_receipt_id", name="uq_series_public_ordinals_receipt"),
        sa.UniqueConstraint("episode_attempt_authority_id", name="uq_series_public_ordinals_attempt"),
        sa.CheckConstraint("public_episode_ordinal > 0", name="ck_series_public_ordinals_positive"),
        sa.CheckConstraint("authority_hash ~ '^[0-9a-f]{64}$'", name="ck_series_public_ordinals_hash"),
    )
    op.create_index("ix_series_public_ordinals_plan", "series_public_ordinal_authorities", ["series_plan_id"])

    op.create_table(
        "series_arc_decision_authorities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("series_plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("series_plans.id"), nullable=False),
        sa.Column("source_arc_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("series_arc_versions.id"), nullable=False),
        sa.Column("target_arc_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("series_arc_versions.id")),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("effective_public_episode_count", sa.Integer()),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("decision_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("decision_hash", name="uq_series_arc_decisions_hash"),
        sa.CheckConstraint("action in ('EARLY_COMPLETION','EXTENSION','COMPLETION')", name="ck_series_arc_decisions_action"),
        sa.CheckConstraint("effective_public_episode_count is null or effective_public_episode_count > 0", name="ck_series_arc_decisions_count"),
        sa.CheckConstraint("decision_hash ~ '^[0-9a-f]{64}$'", name="ck_series_arc_decisions_hash"),
    )
    op.create_index("ix_series_arc_decisions_plan", "series_arc_decision_authorities", ["series_plan_id"])


def downgrade() -> None:
    op.drop_table("series_arc_decision_authorities")
    op.drop_table("series_public_ordinal_authorities")
    op.drop_table("series_episode_attempt_authorities")
    op.drop_table("series_episode_blueprints")
    op.execute("DROP INDEX IF EXISTS uq_series_arc_versions_one_approved")
    op.drop_table("series_arc_versions")
