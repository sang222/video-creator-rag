"""R3D2 effective channel runtime context snapshot

Revision ID: 0025_r3d2_effective_context
Revises: 0024_r3d1_hierarchical_scope
Create Date: 2026-06-30 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0025_r3d2_effective_context"
down_revision: str | None = "0024_r3d1_hierarchical_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "effective_channel_runtime_context_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_profile_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("compiled_policy_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel_contract_hash", sa.Text(), nullable=True),
        sa.Column("field_source_map_hash", sa.Text(), nullable=True),
        sa.Column("content_category_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("character_binding_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("character_policy_mode", sa.String(length=40), nullable=True),
        sa.Column("character_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("character_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("character_image_branch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reference_asset_pack_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("voice_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("compile_status", sa.String(length=40), nullable=False),
        sa.Column("reason_codes_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("source_refs_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("context_hash", sa.Text(), nullable=False),
        sa.Column("market_locale_context_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("audience_context_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("brand_voice_persona_context_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("category_runtime_context_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("character_identity_context_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("visual_style_context_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("voice_audio_context_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("thumbnail_style_context_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("metadata_seo_policy_context_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("publish_timing_context_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("source_rights_disclosure_context_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("monetization_cta_context_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("cost_provider_policy_context_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("safety_forbidden_claims_context_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["channel_profile_version_id"], ["channel_profile_versions.id"]),
        sa.ForeignKeyConstraint(["compiled_policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.ForeignKeyConstraint(["content_category_id"], ["content_categories.id"]),
        sa.ForeignKeyConstraint(["character_binding_id"], ["character_bindings.id"]),
        sa.ForeignKeyConstraint(["character_profile_id"], ["character_profiles.id"]),
        sa.ForeignKeyConstraint(["character_version_id"], ["character_versions.id"]),
        sa.ForeignKeyConstraint(["character_image_branch_id"], ["character_image_branches.id"]),
        sa.ForeignKeyConstraint(["reference_asset_pack_id"], ["character_reference_asset_packs.id"]),
        sa.ForeignKeyConstraint(["voice_profile_id"], ["voice_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_effective_context_video_project", "effective_channel_runtime_context_snapshots", ["video_project_id"])
    op.create_index("ix_effective_context_company", "effective_channel_runtime_context_snapshots", ["company_id"])
    op.create_index("ix_effective_context_channel", "effective_channel_runtime_context_snapshots", ["channel_workspace_id"])
    op.create_index("ix_effective_context_policy_snapshot", "effective_channel_runtime_context_snapshots", ["compiled_policy_snapshot_id"])
    op.create_index("ix_effective_context_category", "effective_channel_runtime_context_snapshots", ["content_category_id"])
    op.create_index("ix_effective_context_character_binding", "effective_channel_runtime_context_snapshots", ["character_binding_id"])
    op.create_index("ix_effective_context_status", "effective_channel_runtime_context_snapshots", ["compile_status"])
    op.create_index("ix_effective_context_hash", "effective_channel_runtime_context_snapshots", ["context_hash"])
    op.create_index("ix_effective_context_created_at", "effective_channel_runtime_context_snapshots", ["created_at"])

    op.create_foreign_key(
        "fk_video_projects_effective_context_snapshot_id",
        "video_projects",
        "effective_channel_runtime_context_snapshots",
        ["effective_context_snapshot_id"],
        ["id"],
    )
    op.create_index("ix_video_projects_effective_context_snapshot", "video_projects", ["effective_context_snapshot_id"])

    op.add_column("first_scripted_video_packages", sa.Column("effective_context_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("first_scripted_video_packages", sa.Column("effective_context_hash", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_first_scripted_video_packages_effective_context_snapshot_id",
        "first_scripted_video_packages",
        "effective_channel_runtime_context_snapshots",
        ["effective_context_snapshot_id"],
        ["id"],
    )
    op.create_index(
        "ix_first_scripted_video_packages_effective_context",
        "first_scripted_video_packages",
        ["effective_context_snapshot_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_first_scripted_video_packages_effective_context", table_name="first_scripted_video_packages")
    op.drop_constraint(
        "fk_first_scripted_video_packages_effective_context_snapshot_id",
        "first_scripted_video_packages",
        type_="foreignkey",
    )
    op.drop_column("first_scripted_video_packages", "effective_context_hash")
    op.drop_column("first_scripted_video_packages", "effective_context_snapshot_id")

    op.drop_index("ix_video_projects_effective_context_snapshot", table_name="video_projects")
    op.drop_constraint("fk_video_projects_effective_context_snapshot_id", "video_projects", type_="foreignkey")

    op.drop_index("ix_effective_context_created_at", table_name="effective_channel_runtime_context_snapshots")
    op.drop_index("ix_effective_context_hash", table_name="effective_channel_runtime_context_snapshots")
    op.drop_index("ix_effective_context_status", table_name="effective_channel_runtime_context_snapshots")
    op.drop_index("ix_effective_context_character_binding", table_name="effective_channel_runtime_context_snapshots")
    op.drop_index("ix_effective_context_category", table_name="effective_channel_runtime_context_snapshots")
    op.drop_index("ix_effective_context_policy_snapshot", table_name="effective_channel_runtime_context_snapshots")
    op.drop_index("ix_effective_context_channel", table_name="effective_channel_runtime_context_snapshots")
    op.drop_index("ix_effective_context_company", table_name="effective_channel_runtime_context_snapshots")
    op.drop_index("ix_effective_context_video_project", table_name="effective_channel_runtime_context_snapshots")
    op.drop_table("effective_channel_runtime_context_snapshots")
