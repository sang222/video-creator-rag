import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class ContentCategory(Base):
    __tablename__ = "content_categories"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    category_key: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sub_niche: Mapped[str | None] = mapped_column(Text)
    audience_segment: Mapped[str | None] = mapped_column(Text)
    content_pillar: Mapped[str | None] = mapped_column(Text)
    default_format_policy_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    default_visual_style_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    default_voice_style_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    default_thumbnail_style_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    visual_mode: Mapped[str | None] = mapped_column(Text)
    character_policy_mode: Mapped[str] = mapped_column(String(40), nullable=False, default="NO_CHARACTER")
    allowed_character_binding_scope: Mapped[str | None] = mapped_column(Text)
    default_memory_scope: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    human_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("company_id", "channel_workspace_id", "category_key"),
        Index("ix_content_categories_company", "company_id"),
        Index("ix_content_categories_channel", "channel_workspace_id"),
        Index("ix_content_categories_status", "status"),
        Index("ix_content_categories_policy_mode", "character_policy_mode"),
        Index("ix_content_categories_created_at", "created_at"),
    )


class CategoryCreativeDigest(Base):
    __tablename__ = "category_creative_digests"

    id: Mapped[uuid.UUID] = uuid_pk()
    content_category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_categories.id"), nullable=False
    )
    digest_version: Mapped[int] = mapped_column(Integer, nullable=False)
    digest_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    digest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    source_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("content_category_id", "digest_version"),
        Index("ix_category_creative_digests_category", "content_category_id"),
        Index("ix_category_creative_digests_created_at", "created_at"),
    )


class CharacterProfile(Base):
    __tablename__ = "character_profiles"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    character_key: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    role_description: Mapped[str | None] = mapped_column(Text)
    persona_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    human_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("company_id", "channel_workspace_id", "character_key"),
        Index("ix_character_profiles_company", "company_id"),
        Index("ix_character_profiles_channel", "channel_workspace_id"),
        Index("ix_character_profiles_status", "status"),
        Index("ix_character_profiles_created_at", "created_at"),
    )


class CharacterVersion(Base):
    __tablename__ = "character_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    character_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("character_profiles.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    identity_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    visual_identity_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    voice_identity_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    continuity_rules_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    human_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("character_profile_id", "version"),
        Index("ix_character_versions_profile", "character_profile_id"),
        Index("ix_character_versions_status", "status"),
        Index("ix_character_versions_created_at", "created_at"),
    )


class CharacterImageBranch(Base):
    __tablename__ = "character_image_branches"

    id: Mapped[uuid.UUID] = uuid_pk()
    character_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("character_versions.id"), nullable=False
    )
    branch_key: Mapped[str] = mapped_column(Text, nullable=False)
    visual_branch_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    provider_constraints_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    human_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("character_version_id", "branch_key"),
        Index("ix_character_image_branches_version", "character_version_id"),
        Index("ix_character_image_branches_status", "status"),
        Index("ix_character_image_branches_created_at", "created_at"),
    )


class CharacterReferenceAssetPack(Base):
    __tablename__ = "character_reference_asset_packs"

    id: Mapped[uuid.UUID] = uuid_pk()
    character_image_branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("character_image_branches.id"), nullable=False
    )
    pack_key: Mapped[str] = mapped_column(Text, nullable=False)
    pack_manifest_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    rights_status: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    prompt_safety_state: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    human_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("character_image_branch_id", "pack_key"),
        Index("ix_character_reference_asset_packs_branch", "character_image_branch_id"),
        Index("ix_character_reference_asset_packs_status", "status"),
        Index("ix_character_reference_asset_packs_rights", "rights_status"),
        Index("ix_character_reference_asset_packs_created_at", "created_at"),
    )


class CharacterReferenceAsset(Base):
    __tablename__ = "character_reference_assets"

    id: Mapped[uuid.UUID] = uuid_pk()
    reference_asset_pack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("character_reference_asset_packs.id"), nullable=False
    )
    asset_type: Mapped[str] = mapped_column(String(40), nullable=False, default="OTHER")
    cloud_media_ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cloud_media_refs.id"))
    local_ref: Mapped[str | None] = mapped_column(Text)
    source_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    rights_status: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    prompt_safety_state: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    checksum_sha256: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_character_reference_assets_pack", "reference_asset_pack_id"),
        Index("ix_character_reference_assets_cloud_ref", "cloud_media_ref_id"),
        Index("ix_character_reference_assets_type", "asset_type"),
        Index("ix_character_reference_assets_created_at", "created_at"),
    )


class VoiceProfile(Base):
    __tablename__ = "voice_profiles"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    character_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("character_profiles.id"))
    voice_key: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False)
    accent: Mapped[str | None] = mapped_column(Text)
    tone_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    pace_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    pronunciation_dictionary_ref: Mapped[str | None] = mapped_column(Text)
    provider_policy_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    consent_status: Mapped[str] = mapped_column(String(40), nullable=False, default="NOT_REQUIRED")
    commercial_use_status: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    human_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("company_id", "channel_workspace_id", "voice_key"),
        Index("ix_voice_profiles_company", "company_id"),
        Index("ix_voice_profiles_channel", "channel_workspace_id"),
        Index("ix_voice_profiles_character", "character_profile_id"),
        Index("ix_voice_profiles_status", "status"),
        Index("ix_voice_profiles_created_at", "created_at"),
    )


class CharacterBinding(Base):
    __tablename__ = "character_bindings"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    content_category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("content_categories.id"))
    character_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("character_profiles.id"), nullable=False
    )
    character_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("character_versions.id"), nullable=False
    )
    character_image_branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("character_image_branches.id")
    )
    reference_asset_pack_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("character_reference_asset_packs.id")
    )
    voice_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("voice_profiles.id"))
    binding_scope: Mapped[str] = mapped_column(String(40), nullable=False, default="CATEGORY")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    human_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_character_bindings_company", "company_id"),
        Index("ix_character_bindings_channel", "channel_workspace_id"),
        Index("ix_character_bindings_category", "content_category_id"),
        Index("ix_character_bindings_character_profile", "character_profile_id"),
        Index("ix_character_bindings_character_version", "character_version_id"),
        Index("ix_character_bindings_status", "status"),
        Index("ix_character_bindings_scope", "binding_scope"),
        Index("ix_character_bindings_created_at", "created_at"),
    )
