"""R3D1 hierarchical scope and character authority

Revision ID: 0024_r3d1_hierarchical_scope
Revises: 0023_m12_2p3_channel_init_drafts
Create Date: 2026-06-30 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0024_r3d1_hierarchical_scope"
down_revision: str | None = "0023_m12_2p3_channel_init_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "content_categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category_key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("sub_niche", sa.Text(), nullable=True),
        sa.Column("audience_segment", sa.Text(), nullable=True),
        sa.Column("content_pillar", sa.Text(), nullable=True),
        sa.Column("default_format_policy_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("default_visual_style_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("default_voice_style_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("default_thumbnail_style_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("visual_mode", sa.Text(), nullable=True),
        sa.Column("character_policy_mode", sa.String(length=40), server_default="NO_CHARACTER", nullable=False),
        sa.Column("allowed_character_binding_scope", sa.Text(), nullable=True),
        sa.Column("default_memory_scope", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=40), server_default="DRAFT", nullable=False),
        sa.Column("human_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_refs_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "channel_workspace_id", "category_key"),
    )
    op.create_index("ix_content_categories_company", "content_categories", ["company_id"])
    op.create_index("ix_content_categories_channel", "content_categories", ["channel_workspace_id"])
    op.create_index("ix_content_categories_status", "content_categories", ["status"])
    op.create_index("ix_content_categories_policy_mode", "content_categories", ["character_policy_mode"])
    op.create_index("ix_content_categories_created_at", "content_categories", ["created_at"])

    op.create_table(
        "category_creative_digests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_category_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("digest_version", sa.Integer(), nullable=False),
        sa.Column("digest_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("digest_hash", sa.Text(), nullable=False),
        sa.Column("source_refs_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["content_category_id"], ["content_categories.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_category_id", "digest_version"),
    )
    op.create_index("ix_category_creative_digests_category", "category_creative_digests", ["content_category_id"])
    op.create_index("ix_category_creative_digests_created_at", "category_creative_digests", ["created_at"])

    op.create_table(
        "character_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("character_key", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("role_description", sa.Text(), nullable=True),
        sa.Column("persona_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("status", sa.String(length=40), server_default="DRAFT", nullable=False),
        sa.Column("human_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_refs_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "channel_workspace_id", "character_key"),
    )
    op.create_index("ix_character_profiles_company", "character_profiles", ["company_id"])
    op.create_index("ix_character_profiles_channel", "character_profiles", ["channel_workspace_id"])
    op.create_index("ix_character_profiles_status", "character_profiles", ["status"])
    op.create_index("ix_character_profiles_created_at", "character_profiles", ["created_at"])

    op.create_table(
        "character_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("character_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("identity_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("visual_identity_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("voice_identity_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("continuity_rules_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("status", sa.String(length=40), server_default="DRAFT", nullable=False),
        sa.Column("human_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["character_profile_id"], ["character_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("character_profile_id", "version"),
    )
    op.create_index("ix_character_versions_profile", "character_versions", ["character_profile_id"])
    op.create_index("ix_character_versions_status", "character_versions", ["status"])
    op.create_index("ix_character_versions_created_at", "character_versions", ["created_at"])

    op.create_table(
        "character_image_branches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("character_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("branch_key", sa.Text(), nullable=False),
        sa.Column("visual_branch_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("provider_constraints_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("status", sa.String(length=40), server_default="DRAFT", nullable=False),
        sa.Column("human_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["character_version_id"], ["character_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("character_version_id", "branch_key"),
    )
    op.create_index("ix_character_image_branches_version", "character_image_branches", ["character_version_id"])
    op.create_index("ix_character_image_branches_status", "character_image_branches", ["status"])
    op.create_index("ix_character_image_branches_created_at", "character_image_branches", ["created_at"])

    op.create_table(
        "character_reference_asset_packs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("character_image_branch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pack_key", sa.Text(), nullable=False),
        sa.Column("pack_manifest_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("rights_status", sa.String(length=40), server_default="UNKNOWN", nullable=False),
        sa.Column("prompt_safety_state", sa.String(length=40), server_default="UNKNOWN", nullable=False),
        sa.Column("status", sa.String(length=40), server_default="DRAFT", nullable=False),
        sa.Column("human_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["character_image_branch_id"], ["character_image_branches.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("character_image_branch_id", "pack_key"),
    )
    op.create_index("ix_character_reference_asset_packs_branch", "character_reference_asset_packs", ["character_image_branch_id"])
    op.create_index("ix_character_reference_asset_packs_status", "character_reference_asset_packs", ["status"])
    op.create_index("ix_character_reference_asset_packs_rights", "character_reference_asset_packs", ["rights_status"])
    op.create_index("ix_character_reference_asset_packs_created_at", "character_reference_asset_packs", ["created_at"])

    op.create_table(
        "character_reference_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reference_asset_pack_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_type", sa.String(length=40), server_default="OTHER", nullable=False),
        sa.Column("cloud_media_ref_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("local_ref", sa.Text(), nullable=True),
        sa.Column("source_refs_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("rights_status", sa.String(length=40), server_default="UNKNOWN", nullable=False),
        sa.Column("prompt_safety_state", sa.String(length=40), server_default="UNKNOWN", nullable=False),
        sa.Column("checksum_sha256", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["reference_asset_pack_id"], ["character_reference_asset_packs.id"]),
        sa.ForeignKeyConstraint(["cloud_media_ref_id"], ["cloud_media_refs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_character_reference_assets_pack", "character_reference_assets", ["reference_asset_pack_id"])
    op.create_index("ix_character_reference_assets_cloud_ref", "character_reference_assets", ["cloud_media_ref_id"])
    op.create_index("ix_character_reference_assets_type", "character_reference_assets", ["asset_type"])
    op.create_index("ix_character_reference_assets_created_at", "character_reference_assets", ["created_at"])

    op.create_table(
        "voice_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("character_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("voice_key", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("accent", sa.Text(), nullable=True),
        sa.Column("tone_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("pace_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("pronunciation_dictionary_ref", sa.Text(), nullable=True),
        sa.Column("provider_policy_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("consent_status", sa.String(length=40), server_default="NOT_REQUIRED", nullable=False),
        sa.Column("commercial_use_status", sa.String(length=40), server_default="UNKNOWN", nullable=False),
        sa.Column("status", sa.String(length=40), server_default="DRAFT", nullable=False),
        sa.Column("human_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["character_profile_id"], ["character_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "channel_workspace_id", "voice_key"),
    )
    op.create_index("ix_voice_profiles_company", "voice_profiles", ["company_id"])
    op.create_index("ix_voice_profiles_channel", "voice_profiles", ["channel_workspace_id"])
    op.create_index("ix_voice_profiles_character", "voice_profiles", ["character_profile_id"])
    op.create_index("ix_voice_profiles_status", "voice_profiles", ["status"])
    op.create_index("ix_voice_profiles_created_at", "voice_profiles", ["created_at"])

    op.create_table(
        "character_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_category_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("character_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("character_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("character_image_branch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reference_asset_pack_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("voice_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("binding_scope", sa.String(length=40), server_default="CATEGORY", nullable=False),
        sa.Column("status", sa.String(length=40), server_default="DRAFT", nullable=False),
        sa.Column("human_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["content_category_id"], ["content_categories.id"]),
        sa.ForeignKeyConstraint(["character_profile_id"], ["character_profiles.id"]),
        sa.ForeignKeyConstraint(["character_version_id"], ["character_versions.id"]),
        sa.ForeignKeyConstraint(["character_image_branch_id"], ["character_image_branches.id"]),
        sa.ForeignKeyConstraint(["reference_asset_pack_id"], ["character_reference_asset_packs.id"]),
        sa.ForeignKeyConstraint(["voice_profile_id"], ["voice_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_character_bindings_company", "character_bindings", ["company_id"])
    op.create_index("ix_character_bindings_channel", "character_bindings", ["channel_workspace_id"])
    op.create_index("ix_character_bindings_category", "character_bindings", ["content_category_id"])
    op.create_index("ix_character_bindings_character_profile", "character_bindings", ["character_profile_id"])
    op.create_index("ix_character_bindings_character_version", "character_bindings", ["character_version_id"])
    op.create_index("ix_character_bindings_status", "character_bindings", ["status"])
    op.create_index("ix_character_bindings_scope", "character_bindings", ["binding_scope"])
    op.create_index("ix_character_bindings_created_at", "character_bindings", ["created_at"])

    op.add_column("editorial_calendar_slots", sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("editorial_calendar_slots", sa.Column("character_binding_policy_json", JSONB, nullable=True))
    op.create_foreign_key(
        "fk_editorial_calendar_slots_category_id",
        "editorial_calendar_slots",
        "content_categories",
        ["category_id"],
        ["id"],
    )
    op.create_index("ix_editorial_calendar_slots_category_id", "editorial_calendar_slots", ["category_id"])

    op.add_column("video_projects", sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("video_projects", sa.Column("character_binding_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("video_projects", sa.Column("channel_contract_content_hash", sa.Text(), nullable=True))
    op.add_column("video_projects", sa.Column("effective_context_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_video_projects_category_id", "video_projects", "content_categories", ["category_id"], ["id"])
    op.create_foreign_key("fk_video_projects_character_binding_id", "video_projects", "character_bindings", ["character_binding_id"], ["id"])
    op.create_index("ix_video_projects_category_id", "video_projects", ["category_id"])
    op.create_index("ix_video_projects_character_binding_id", "video_projects", ["character_binding_id"])
    op.create_index("ix_video_projects_channel_contract_hash", "video_projects", ["channel_contract_content_hash"])


def downgrade() -> None:
    op.drop_index("ix_video_projects_channel_contract_hash", table_name="video_projects")
    op.drop_index("ix_video_projects_character_binding_id", table_name="video_projects")
    op.drop_index("ix_video_projects_category_id", table_name="video_projects")
    op.drop_constraint("fk_video_projects_character_binding_id", "video_projects", type_="foreignkey")
    op.drop_constraint("fk_video_projects_category_id", "video_projects", type_="foreignkey")
    op.drop_column("video_projects", "effective_context_snapshot_id")
    op.drop_column("video_projects", "channel_contract_content_hash")
    op.drop_column("video_projects", "character_binding_id")
    op.drop_column("video_projects", "category_id")

    op.drop_index("ix_editorial_calendar_slots_category_id", table_name="editorial_calendar_slots")
    op.drop_constraint("fk_editorial_calendar_slots_category_id", "editorial_calendar_slots", type_="foreignkey")
    op.drop_column("editorial_calendar_slots", "character_binding_policy_json")
    op.drop_column("editorial_calendar_slots", "category_id")

    op.drop_index("ix_character_bindings_created_at", table_name="character_bindings")
    op.drop_index("ix_character_bindings_scope", table_name="character_bindings")
    op.drop_index("ix_character_bindings_status", table_name="character_bindings")
    op.drop_index("ix_character_bindings_character_version", table_name="character_bindings")
    op.drop_index("ix_character_bindings_character_profile", table_name="character_bindings")
    op.drop_index("ix_character_bindings_category", table_name="character_bindings")
    op.drop_index("ix_character_bindings_channel", table_name="character_bindings")
    op.drop_index("ix_character_bindings_company", table_name="character_bindings")
    op.drop_table("character_bindings")

    op.drop_index("ix_voice_profiles_created_at", table_name="voice_profiles")
    op.drop_index("ix_voice_profiles_status", table_name="voice_profiles")
    op.drop_index("ix_voice_profiles_character", table_name="voice_profiles")
    op.drop_index("ix_voice_profiles_channel", table_name="voice_profiles")
    op.drop_index("ix_voice_profiles_company", table_name="voice_profiles")
    op.drop_table("voice_profiles")

    op.drop_index("ix_character_reference_assets_created_at", table_name="character_reference_assets")
    op.drop_index("ix_character_reference_assets_type", table_name="character_reference_assets")
    op.drop_index("ix_character_reference_assets_cloud_ref", table_name="character_reference_assets")
    op.drop_index("ix_character_reference_assets_pack", table_name="character_reference_assets")
    op.drop_table("character_reference_assets")

    op.drop_index("ix_character_reference_asset_packs_created_at", table_name="character_reference_asset_packs")
    op.drop_index("ix_character_reference_asset_packs_rights", table_name="character_reference_asset_packs")
    op.drop_index("ix_character_reference_asset_packs_status", table_name="character_reference_asset_packs")
    op.drop_index("ix_character_reference_asset_packs_branch", table_name="character_reference_asset_packs")
    op.drop_table("character_reference_asset_packs")

    op.drop_index("ix_character_image_branches_created_at", table_name="character_image_branches")
    op.drop_index("ix_character_image_branches_status", table_name="character_image_branches")
    op.drop_index("ix_character_image_branches_version", table_name="character_image_branches")
    op.drop_table("character_image_branches")

    op.drop_index("ix_character_versions_created_at", table_name="character_versions")
    op.drop_index("ix_character_versions_status", table_name="character_versions")
    op.drop_index("ix_character_versions_profile", table_name="character_versions")
    op.drop_table("character_versions")

    op.drop_index("ix_character_profiles_created_at", table_name="character_profiles")
    op.drop_index("ix_character_profiles_status", table_name="character_profiles")
    op.drop_index("ix_character_profiles_channel", table_name="character_profiles")
    op.drop_index("ix_character_profiles_company", table_name="character_profiles")
    op.drop_table("character_profiles")

    op.drop_index("ix_category_creative_digests_created_at", table_name="category_creative_digests")
    op.drop_index("ix_category_creative_digests_category", table_name="category_creative_digests")
    op.drop_table("category_creative_digests")

    op.drop_index("ix_content_categories_created_at", table_name="content_categories")
    op.drop_index("ix_content_categories_policy_mode", table_name="content_categories")
    op.drop_index("ix_content_categories_status", table_name="content_categories")
    op.drop_index("ix_content_categories_channel", table_name="content_categories")
    op.drop_index("ix_content_categories_company", table_name="content_categories")
    op.drop_table("content_categories")
