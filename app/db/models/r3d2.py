import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, uuid_pk


class EffectiveChannelRuntimeContextSnapshot(Base):
    __tablename__ = "effective_channel_runtime_context_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    channel_profile_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_profile_versions.id"))
    compiled_policy_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id"))
    channel_contract_hash: Mapped[str | None] = mapped_column(Text)
    field_source_map_hash: Mapped[str | None] = mapped_column(Text)
    content_category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("content_categories.id"))
    character_binding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("character_bindings.id"))
    character_policy_mode: Mapped[str | None] = mapped_column(String(40))
    character_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("character_profiles.id"))
    character_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("character_versions.id"))
    character_image_branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("character_image_branches.id"))
    reference_asset_pack_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("character_reference_asset_packs.id"))
    voice_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("voice_profiles.id"))
    compile_status: Mapped[str] = mapped_column(String(40), nullable=False)
    reason_codes_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    source_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    context_hash: Mapped[str] = mapped_column(Text, nullable=False)
    market_locale_context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    audience_context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    brand_voice_persona_context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    category_runtime_context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    character_identity_context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    visual_style_context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    voice_audio_context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    thumbnail_style_context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    metadata_seo_policy_context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    publish_timing_context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_rights_disclosure_context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    monetization_cta_context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    cost_provider_policy_context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    safety_forbidden_claims_context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_effective_context_video_project", "video_project_id"),
        Index("ix_effective_context_company", "company_id"),
        Index("ix_effective_context_channel", "channel_workspace_id"),
        Index("ix_effective_context_policy_snapshot", "compiled_policy_snapshot_id"),
        Index("ix_effective_context_category", "content_category_id"),
        Index("ix_effective_context_character_binding", "character_binding_id"),
        Index("ix_effective_context_status", "compile_status"),
        Index("ix_effective_context_hash", "context_hash"),
        Index("ix_effective_context_created_at", "created_at"),
    )
