"""add durable series arc and public ordinal authority

Revision ID: 0085_series_authority
Revises: 0084_youtube_private_delivery
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0085_series_authority"
down_revision = "0084_youtube_private_delivery"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "series_arc_versions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("series_plan_id", UUID, nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("previous_version_id", UUID, nullable=True),
        sa.Column("arc_mode", sa.String(length=24), nullable=False),
        sa.Column("planned_episode_count", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("premise", sa.Text(), nullable=False),
        sa.Column("coverage_policy", JSONB, nullable=False),
        sa.Column("approved_by", UUID, nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "arc_mode IN ('FIXED_COUNT','ROLLING')", name="ck_series_arc_mode"
        ),
        sa.CheckConstraint(
            "state IN ('DRAFT','ACTIVE','SUPERSEDED','COMPLETION_PENDING','COMPLETED')",
            name="ck_series_arc_state",
        ),
        sa.CheckConstraint(
            "(arc_mode = 'FIXED_COUNT' AND planned_episode_count > 0) OR "
            "(arc_mode = 'ROLLING' AND planned_episode_count IS NULL)",
            name="ck_series_arc_planned_count",
        ),
        sa.UniqueConstraint(
            "series_plan_id", "version_number", name="uq_series_arc_version_number"
        ),
    )
    op.create_index("ix_series_arc_company", "series_arc_versions", ["company_id"])
    op.create_index(
        "ix_series_arc_channel", "series_arc_versions", ["channel_workspace_id"]
    )
    op.create_index("ix_series_arc_plan", "series_arc_versions", ["series_plan_id"])
    op.create_index("ix_series_arc_state", "series_arc_versions", ["state"])
    op.create_index("ix_series_arc_hash", "series_arc_versions", ["content_hash"])
    op.create_index(
        "uq_series_arc_one_current",
        "series_arc_versions",
        ["series_plan_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('ACTIVE','COMPLETION_PENDING')"),
    )

    op.create_table(
        "series_episode_blueprints",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("series_plan_id", UUID, nullable=False),
        sa.Column("series_arc_version_id", UUID, nullable=False),
        sa.Column("blueprint_key", sa.String(length=160), nullable=False),
        sa.Column("planned_position", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("editorial_contract", JSONB, nullable=False),
        sa.Column("coverage_tags", JSONB, nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("video_project_id", UUID, nullable=True),
        sa.Column("technical_attempt_ref", sa.String(length=240), nullable=True),
        sa.Column("publication_receipt_id", UUID, nullable=True),
        sa.Column("public_ordinal", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('PLANNED','ASSIGNED','PUBLISHED','SKIPPED')",
            name="ck_series_blueprint_state",
        ),
        sa.CheckConstraint(
            "planned_position IS NULL OR planned_position > 0",
            name="ck_series_blueprint_position",
        ),
        sa.UniqueConstraint(
            "series_arc_version_id", "blueprint_key", name="uq_series_blueprint_key"
        ),
        sa.UniqueConstraint(
            "series_arc_version_id",
            "planned_position",
            name="uq_series_blueprint_position",
        ),
    )
    op.create_index(
        "ix_series_blueprint_arc", "series_episode_blueprints", ["series_arc_version_id"]
    )
    op.create_index(
        "ix_series_blueprint_plan", "series_episode_blueprints", ["series_plan_id"]
    )
    op.create_index(
        "ix_series_blueprint_project", "series_episode_blueprints", ["video_project_id"]
    )
    op.create_index(
        "ix_series_blueprint_receipt",
        "series_episode_blueprints",
        ["publication_receipt_id"],
    )

    op.create_table(
        "series_lifecycle_decisions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("series_plan_id", UUID, nullable=False),
        sa.Column("series_arc_version_id", UUID, nullable=False),
        sa.Column("decision_type", sa.String(length=32), nullable=False),
        sa.Column("command_id", UUID, nullable=False),
        sa.Column("actor_id", UUID, nullable=False),
        sa.Column("previous_count", sa.Integer(), nullable=True),
        sa.Column("resulting_count", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("evidence_refs", JSONB, nullable=False),
        sa.Column("decision_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision_type IN ('ACTIVATE','EARLY_COMPLETE','EXTEND','COMPLETE')",
            name="ck_series_lifecycle_decision_type",
        ),
        sa.CheckConstraint("state = 'APPROVED'", name="ck_series_lifecycle_state"),
        sa.UniqueConstraint("command_id", name="uq_series_lifecycle_command"),
    )
    op.create_index(
        "ix_series_lifecycle_plan", "series_lifecycle_decisions", ["series_plan_id"]
    )
    op.create_index(
        "ix_series_lifecycle_arc", "series_lifecycle_decisions", ["series_arc_version_id"]
    )

    op.create_table(
        "series_public_ordinals",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("series_plan_id", UUID, nullable=False),
        sa.Column("series_arc_version_id", UUID, nullable=False),
        sa.Column("episode_blueprint_id", UUID, nullable=False),
        sa.Column("video_project_id", UUID, nullable=False),
        sa.Column("publication_receipt_id", UUID, nullable=False),
        sa.Column("public_ordinal", sa.Integer(), nullable=False),
        sa.Column("playlist_position", sa.Integer(), nullable=False),
        sa.Column("technical_attempt_ref", sa.String(length=240), nullable=True),
        sa.Column("identity_hash", sa.String(length=64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("public_ordinal > 0", name="ck_series_public_ordinal_positive"),
        sa.CheckConstraint(
            "playlist_position = public_ordinal - 1",
            name="ck_series_playlist_position_matches_ordinal",
        ),
        sa.UniqueConstraint(
            "series_plan_id", "public_ordinal", name="uq_series_public_ordinal"
        ),
        sa.UniqueConstraint(
            "series_plan_id",
            "publication_receipt_id",
            name="uq_series_public_receipt",
        ),
        sa.UniqueConstraint(
            "series_plan_id", "video_project_id", name="uq_series_public_project"
        ),
    )
    op.create_index(
        "ix_series_public_plan", "series_public_ordinals", ["series_plan_id"]
    )
    op.create_index(
        "ix_series_public_blueprint", "series_public_ordinals", ["episode_blueprint_id"]
    )
    op.create_index(
        "ix_series_public_receipt", "series_public_ordinals", ["publication_receipt_id"]
    )


def downgrade() -> None:
    op.drop_table("series_public_ordinals")
    op.drop_table("series_lifecycle_decisions")
    op.drop_table("series_episode_blueprints")
    op.drop_index("uq_series_arc_one_current", table_name="series_arc_versions")
    op.drop_table("series_arc_versions")
