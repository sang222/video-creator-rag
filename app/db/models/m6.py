import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class ProductionArtifactRun(Base):
    __tablename__ = "production_artifact_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id"), nullable=False
    )
    source_project_admission_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_admission_decisions.id")
    )
    run_mode: Mapped[str] = mapped_column(String(40), nullable=False, default="REAL_DISABLED")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="PENDING")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    script_artifact_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("artifact_versions.id"))
    voice_timeline_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    caption_track_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    visual_plan_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    scene_manifest_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    render_spec_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    asset_manifest_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_manifest_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    render_package_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    media_qc_report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    accessibility_qc_report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_production_artifact_runs_company_id", "company_id"),
        Index("ix_production_artifact_runs_channel_workspace_id", "channel_workspace_id"),
        Index("ix_production_artifact_runs_video_project_id", "video_project_id"),
        Index("ix_production_artifact_runs_policy_snapshot_id", "policy_snapshot_id"),
        Index("ix_production_artifact_runs_status", "status"),
        Index("ix_production_artifact_runs_created_at", "created_at"),
    )


class VoiceTimelineSnapshot(Base):
    __tablename__ = "voice_timeline_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    production_artifact_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_artifact_runs.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    script_artifact_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("artifact_versions.id"))
    policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id"), nullable=False
    )
    timeline_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    total_duration_seconds: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    timing_source: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False)
    timeline_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_voice_timeline_snapshots_run_id", "production_artifact_run_id"),
        Index("ix_voice_timeline_snapshots_video_project_id", "video_project_id"),
        Index("ix_voice_timeline_snapshots_created_at", "created_at"),
    )


class CaptionTrackSnapshot(Base):
    __tablename__ = "caption_track_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    production_artifact_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_artifact_runs.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    voice_timeline_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("voice_timeline_snapshots.id"), nullable=False
    )
    caption_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    srt_text: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(40))
    caption_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_caption_track_snapshots_run_id", "production_artifact_run_id"),
        Index("ix_caption_track_snapshots_video_project_id", "video_project_id"),
        Index("ix_caption_track_snapshots_voice_timeline_id", "voice_timeline_snapshot_id"),
        Index("ix_caption_track_snapshots_created_at", "created_at"),
    )


class VisualPlanSnapshot(Base):
    __tablename__ = "visual_plan_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    production_artifact_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_artifact_runs.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    voice_timeline_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("voice_timeline_snapshots.id"), nullable=False
    )
    caption_track_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("caption_track_snapshots.id"))
    visual_plan_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    visual_plan_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_visual_plan_snapshots_run_id", "production_artifact_run_id"),
        Index("ix_visual_plan_snapshots_video_project_id", "video_project_id"),
        Index("ix_visual_plan_snapshots_voice_timeline_id", "voice_timeline_snapshot_id"),
        Index("ix_visual_plan_snapshots_created_at", "created_at"),
    )


class SceneManifestSnapshot(Base):
    __tablename__ = "scene_manifest_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    production_artifact_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_artifact_runs.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    visual_plan_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("visual_plan_snapshots.id"), nullable=False
    )
    scene_manifest_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    scene_manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_scene_manifest_snapshots_run_id", "production_artifact_run_id"),
        Index("ix_scene_manifest_snapshots_video_project_id", "video_project_id"),
        Index("ix_scene_manifest_snapshots_visual_plan_id", "visual_plan_snapshot_id"),
        Index("ix_scene_manifest_snapshots_created_at", "created_at"),
    )


class AssetManifestSnapshot(Base):
    __tablename__ = "asset_manifest_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    production_artifact_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_artifact_runs.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    scene_manifest_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scene_manifest_snapshots.id"), nullable=False
    )
    asset_manifest_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    asset_manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_asset_manifest_snapshots_run_id", "production_artifact_run_id"),
        Index("ix_asset_manifest_snapshots_video_project_id", "video_project_id"),
        Index("ix_asset_manifest_snapshots_scene_manifest_id", "scene_manifest_snapshot_id"),
        Index("ix_asset_manifest_snapshots_created_at", "created_at"),
    )


class SourceManifestSnapshot(Base):
    __tablename__ = "source_manifest_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    production_artifact_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_artifact_runs.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    asset_manifest_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("asset_manifest_snapshots.id"), nullable=False
    )
    source_manifest_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_source_manifest_snapshots_run_id", "production_artifact_run_id"),
        Index("ix_source_manifest_snapshots_video_project_id", "video_project_id"),
        Index("ix_source_manifest_snapshots_asset_manifest_id", "asset_manifest_snapshot_id"),
        Index("ix_source_manifest_snapshots_created_at", "created_at"),
    )


class RenderSpecSnapshot(Base):
    __tablename__ = "render_spec_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    production_artifact_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_artifact_runs.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    voice_timeline_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("voice_timeline_snapshots.id"), nullable=False
    )
    visual_plan_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("visual_plan_snapshots.id"), nullable=False
    )
    caption_track_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("caption_track_snapshots.id"), nullable=False
    )
    asset_manifest_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("asset_manifest_snapshots.id"), nullable=False
    )
    scene_manifest_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("scene_manifest_snapshots.id"))
    render_spec_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    render_spec_hash: Mapped[str] = mapped_column(Text, nullable=False)
    validation_state: Mapped[str] = mapped_column(String(40), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_render_spec_snapshots_run_id", "production_artifact_run_id"),
        Index("ix_render_spec_snapshots_video_project_id", "video_project_id"),
        Index("ix_render_spec_snapshots_validation_state", "validation_state"),
        Index("ix_render_spec_snapshots_created_at", "created_at"),
    )


class MediaRenderJob(Base):
    __tablename__ = "media_render_jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    production_artifact_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("production_artifact_runs.id"))
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    render_spec_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("render_spec_snapshots.id"), nullable=False
    )
    render_variant_id: Mapped[str | None] = mapped_column(String(120))
    renderer_key: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="PENDING")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    output_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(160))
    error_message_redacted: Mapped[str | None] = mapped_column(Text)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_media_render_jobs_run_id", "production_artifact_run_id"),
        Index("ix_media_render_jobs_video_project_id", "video_project_id"),
        Index("ix_media_render_jobs_render_spec_id", "render_spec_snapshot_id"),
        Index("ix_media_render_jobs_status", "status"),
        Index("ix_media_render_jobs_created_at", "created_at"),
    )


class RenderPackageSnapshot(Base):
    __tablename__ = "render_package_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    production_artifact_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("production_artifact_runs.id"))
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    media_render_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("media_render_jobs.id"), nullable=False)
    render_spec_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("render_spec_snapshots.id"), nullable=False
    )
    final_video_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    thumbnail_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    caption_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    manifest_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    file_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    checksum_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    variant_outputs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    package_state: Mapped[str] = mapped_column(String(40), nullable=False, default="CREATED")
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_render_package_snapshots_run_id", "production_artifact_run_id"),
        Index("ix_render_package_snapshots_video_project_id", "video_project_id"),
        Index("ix_render_package_snapshots_render_spec_id", "render_spec_snapshot_id"),
        Index("ix_render_package_snapshots_created_at", "created_at"),
    )


class MediaQCReport(Base):
    __tablename__ = "media_qc_reports"

    id: Mapped[uuid.UUID] = uuid_pk()
    production_artifact_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("production_artifact_runs.id"))
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    render_package_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("render_package_snapshots.id"))
    render_spec_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("render_spec_snapshots.id"), nullable=False
    )
    qc_state: Mapped[str] = mapped_column(String(40), nullable=False)
    duration_check: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    scene_coverage_check: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    caption_alignment_check: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    audio_presence_check: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    file_integrity_check: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    manifest_check: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    variant_check: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_media_qc_reports_run_id", "production_artifact_run_id"),
        Index("ix_media_qc_reports_video_project_id", "video_project_id"),
        Index("ix_media_qc_reports_state", "qc_state"),
        Index("ix_media_qc_reports_created_at", "created_at"),
    )


class AccessibilityQCReport(Base):
    __tablename__ = "accessibility_qc_reports"

    id: Mapped[uuid.UUID] = uuid_pk()
    production_artifact_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("production_artifact_runs.id"))
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    caption_track_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("caption_track_snapshots.id"))
    render_package_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("render_package_snapshots.id"))
    qc_state: Mapped[str] = mapped_column(String(40), nullable=False)
    caption_presence_check: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    caption_readability_check: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    safe_area_check: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    flashing_risk_check: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    disclosure_placement_check: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    pronunciation_check: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_accessibility_qc_reports_run_id", "production_artifact_run_id"),
        Index("ix_accessibility_qc_reports_video_project_id", "video_project_id"),
        Index("ix_accessibility_qc_reports_state", "qc_state"),
        Index("ix_accessibility_qc_reports_created_at", "created_at"),
    )


class PronunciationDictionaryEntry(Base):
    __tablename__ = "pronunciation_dictionary_entries"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"))
    term: Mapped[str] = mapped_column(Text, nullable=False)
    pronunciation_hint: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(40))
    source_ref: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_pronunciation_dictionary_entries_company_id", "company_id"),
        Index("ix_pronunciation_dictionary_entries_channel_id", "channel_workspace_id"),
        Index("ix_pronunciation_dictionary_entries_status", "status"),
        Index("ix_pronunciation_dictionary_entries_created_at", "created_at"),
    )
