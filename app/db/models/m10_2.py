import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class MediaProviderRoleProfile(Base):
    __tablename__ = "media_provider_role_profiles"

    id: Mapped[uuid.UUID] = uuid_pk()
    provider_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    provider_name: Mapped[str] = mapped_column(Text, nullable=False)
    provider_type: Mapped[str] = mapped_column(String(80), nullable=False)
    role_description: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(String(40), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_real_provider: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supports_real_execution: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    monthly_budget_assumption: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_media_provider_roles_type", "provider_type"),
        Index("ix_media_provider_roles_recommendation", "recommendation"),
    )


class ProviderCapabilityMatrixEntry(Base):
    __tablename__ = "provider_capability_matrix_entries"

    id: Mapped[uuid.UUID] = uuid_pk()
    provider_key: Mapped[str] = mapped_column(String(160), ForeignKey("media_provider_role_profiles.provider_key"), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(80), nullable=False)
    job_type: Mapped[str] = mapped_column(String(80), nullable=False)
    capability: Mapped[str] = mapped_column(String(40), nullable=False)
    max_duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    supported_aspect_ratios: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    supported_outputs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    plan_requirement: Mapped[str | None] = mapped_column(String(120))
    capability_reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("provider_key", "job_type", name="uq_provider_capability_provider_job"),
        Index("ix_provider_capability_provider_type", "provider_type"),
        Index("ix_provider_capability_job_type", "job_type"),
        Index("ix_provider_capability_capability", "capability"),
    )


class MediaRenderRoutingDecision(Base):
    __tablename__ = "media_render_routing_decisions"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"))
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"))
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    job_type: Mapped[str] = mapped_column(String(80), nullable=False)
    requested_provider_type: Mapped[str | None] = mapped_column(String(80))
    selected_provider_type: Mapped[str | None] = mapped_column(String(80))
    selected_provider_key: Mapped[str | None] = mapped_column(String(160))
    routing_result: Mapped[str] = mapped_column(String(80), nullable=False)
    blocker_reason: Mapped[str | None] = mapped_column(Text)
    capability_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("provider_capability_matrix_entries.id")
    )
    budget_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("media_provider_budget_snapshots.id"))
    technical_appendix: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_media_routing_decisions_company", "company_id"),
        Index("ix_media_routing_decisions_project", "video_project_id"),
        Index("ix_media_routing_decisions_job", "job_type"),
        Index("ix_media_routing_decisions_result", "routing_result"),
    )


class MediaProviderBudgetPolicy(Base):
    __tablename__ = "media_provider_budget_policies"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"))
    provider_type: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_key: Mapped[str | None] = mapped_column(String(160))
    monthly_cap_units: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    monthly_cap_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    monthly_cap_seconds: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    monthly_cap_renders: Mapped[int | None] = mapped_column(Integer)
    current_mode: Mapped[str] = mapped_column(String(40), nullable=False, default="QUALITY_FIRST_250")
    enforcement: Mapped[str] = mapped_column(String(40), nullable=False, default="REVIEW_REQUIRED")
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_media_budget_policies_company", "company_id"),
        Index("ix_media_budget_policies_provider", "provider_type", "provider_key"),
    )


class MediaProviderBudgetSnapshot(Base):
    __tablename__ = "media_provider_budget_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"))
    provider_type: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_key: Mapped[str | None] = mapped_column(String(160))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    estimated_usage_units: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    estimated_usage_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    estimated_usage_seconds: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    estimated_render_count: Mapped[int | None] = mapped_column(Integer)
    budget_state: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_media_budget_snapshots_company", "company_id"),
        Index("ix_media_budget_snapshots_provider", "provider_type", "provider_key"),
        Index("ix_media_budget_snapshots_period", "period_start", "period_end"),
    )


class LongFormRenderPackage(Base):
    __tablename__ = "long_form_render_packages"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False)
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    voice_timeline_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("voice_timeline_snapshots.id"))
    caption_track_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("caption_track_snapshots.id"))
    visual_plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("visual_plan_snapshots.id"))
    ai_hero_asset_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    creatomate_asset_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    approved_asset_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    thumbnail_variant_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    music_sfx_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    cloud_media_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    render_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    final_renderer_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    final_renderer_provider_key: Mapped[str | None] = mapped_column(String(160))
    package_state: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_long_form_render_packages_company", "company_id"),
        Index("ix_long_form_render_packages_project", "video_project_id"),
        Index("ix_long_form_render_packages_state", "package_state"),
    )


class ShortRenderPackage(Base):
    __tablename__ = "short_render_packages"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False)
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    short_candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("short_candidates.id"))
    short_render_plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("short_render_plans.id"))
    voice_ref: Mapped[str | None] = mapped_column(Text)
    caption_track_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("caption_track_snapshots.id"))
    hero_reuse_ref: Mapped[str | None] = mapped_column(Text)
    template_asset_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    cloud_media_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    render_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    target_duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    target_aspect_ratio: Mapped[str] = mapped_column(String(20), nullable=False, default="9:16")
    hard_cap_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=59)
    renderer_provider_key: Mapped[str | None] = mapped_column(String(160))
    package_state: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_short_render_packages_company", "company_id"),
        Index("ix_short_render_packages_candidate", "short_candidate_id"),
        Index("ix_short_render_packages_state", "package_state"),
    )


class AIHeroAsset(Base):
    __tablename__ = "ai_hero_assets"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False)
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    intended_usage: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(80), nullable=False, default="AI_VIDEO_HERO_PROVIDER")
    provider_key: Mapped[str | None] = mapped_column(String(160))
    duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    asset_ref: Mapped[str | None] = mapped_column(Text)
    still_frame_ref: Mapped[str | None] = mapped_column(Text)
    rights_evidence_ref: Mapped[str | None] = mapped_column(Text)
    generation_state: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_ai_hero_assets_company", "company_id"),
        Index("ix_ai_hero_assets_project", "video_project_id"),
        Index("ix_ai_hero_assets_state", "generation_state"),
    )


class CreatomateRenderAsset(Base):
    __tablename__ = "creatomate_render_assets"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False)
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    short_candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("short_candidates.id"))
    job_type: Mapped[str] = mapped_column(String(80), nullable=False)
    template_key: Mapped[str | None] = mapped_column(String(160))
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output_ref: Mapped[str | None] = mapped_column(Text)
    provider_type: Mapped[str] = mapped_column(String(80), nullable=False, default="CLOUD_TEMPLATE_RENDERER_LIGHT")
    provider_key: Mapped[str | None] = mapped_column(String(160))
    render_state: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_creatomate_assets_company", "company_id"),
        Index("ix_creatomate_assets_project", "video_project_id"),
        Index("ix_creatomate_assets_job", "job_type"),
        Index("ix_creatomate_assets_state", "render_state"),
    )


class ThumbnailVariant(Base):
    __tablename__ = "thumbnail_variants"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False)
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    variant_label: Mapped[str] = mapped_column(String(160), nullable=False)
    title_text: Mapped[str] = mapped_column(Text, nullable=False)
    subtitle_text: Mapped[str | None] = mapped_column(Text)
    hero_still_ref: Mapped[str | None] = mapped_column(Text)
    output_ref: Mapped[str | None] = mapped_column(Text)
    provider_type: Mapped[str] = mapped_column(String(80), nullable=False, default="CLOUD_TEMPLATE_RENDERER_LIGHT")
    provider_key: Mapped[str | None] = mapped_column(String(160))
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_thumbnail_variants_company", "company_id"),
        Index("ix_thumbnail_variants_project", "video_project_id"),
        Index("ix_thumbnail_variants_state", "state"),
    )


class FinalMediaRef(Base):
    __tablename__ = "final_media_refs"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False)
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    uploaded_video_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"))
    media_type: Mapped[str] = mapped_column(String(40), nullable=False)
    file_ref: Mapped[str] = mapped_column(Text, nullable=False)
    duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    aspect_ratio: Mapped[str | None] = mapped_column(String(20))
    resolution: Mapped[str | None] = mapped_column(String(40))
    provider_key: Mapped[str | None] = mapped_column(String(160))
    provider_type: Mapped[str | None] = mapped_column(String(80))
    media_qc_report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("media_qc_reports.id"))
    cloud_media_ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cloud_media_refs.id"))
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_final_media_refs_company", "company_id"),
        Index("ix_final_media_refs_project", "video_project_id"),
        Index("ix_final_media_refs_type", "media_type"),
        Index("ix_final_media_refs_cloud_media_ref", "cloud_media_ref_id"),
    )


class LicenseEvidenceRecord(Base):
    __tablename__ = "license_evidence_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"))
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    asset_ref: Mapped[str] = mapped_column(Text, nullable=False)
    source_provider_type: Mapped[str] = mapped_column(String(80), nullable=False)
    license_status: Mapped[str] = mapped_column(String(40), nullable=False)
    rights_envelope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    evidence_text: Mapped[str | None] = mapped_column(Text)
    evidence_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_license_evidence_company", "company_id"),
        Index("ix_license_evidence_project", "video_project_id"),
        Index("ix_license_evidence_asset_ref", "asset_ref"),
        Index("ix_license_evidence_status", "license_status"),
    )
