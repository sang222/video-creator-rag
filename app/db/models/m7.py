import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class PublishHandoffPackage(Base):
    __tablename__ = "publish_handoff_packages"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id"), nullable=False
    )
    production_artifact_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_artifact_runs.id")
    )
    render_package_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("render_package_snapshots.id"), nullable=False
    )
    render_spec_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("render_spec_snapshots.id"))
    media_qc_report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("media_qc_reports.id"))
    accessibility_qc_report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accessibility_qc_reports.id"))
    source_manifest_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_manifest_snapshots.id")
    )
    asset_manifest_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("asset_manifest_snapshots.id")
    )
    target_platform: Mapped[str] = mapped_column(String(40), nullable=False)
    target_surface: Mapped[str] = mapped_column(String(40), nullable=False)
    destination_binding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    render_variant_id: Mapped[str | None] = mapped_column(String(120))
    package_state: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    planned_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    planned_disclosures: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    planned_files: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    cloud_media_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    checklist_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    operator_instructions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    risk_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    next_action: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_publish_handoff_packages_company_id", "company_id"),
        Index("ix_publish_handoff_packages_channel_workspace_id", "channel_workspace_id"),
        Index("ix_publish_handoff_packages_video_project_id", "video_project_id"),
        Index("ix_publish_handoff_packages_render_package_id", "render_package_snapshot_id"),
        Index("ix_publish_handoff_packages_state", "package_state"),
        Index("ix_publish_handoff_packages_platform", "target_platform"),
        Index("ix_publish_handoff_packages_created_at", "created_at"),
    )


class ManualPublishConfirmation(Base):
    __tablename__ = "manual_publish_confirmations"

    id: Mapped[uuid.UUID] = uuid_pk()
    publish_handoff_package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("publish_handoff_packages.id"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id"), nullable=False
    )
    target_platform: Mapped[str] = mapped_column(String(40), nullable=False)
    target_surface: Mapped[str] = mapped_column(String(40), nullable=False)
    confirmed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    confirmation_state: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    actual_video_id: Mapped[str | None] = mapped_column(Text)
    actual_video_url: Mapped[str | None] = mapped_column(Text)
    actual_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    actual_disclosures: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    actual_files: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    operator_notes: Mapped[str | None] = mapped_column(Text)
    validation_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    metadata_diff: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    next_action: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_manual_publish_confirmations_handoff_id", "publish_handoff_package_id"),
        Index("ix_manual_publish_confirmations_channel_workspace_id", "channel_workspace_id"),
        Index("ix_manual_publish_confirmations_video_project_id", "video_project_id"),
        Index("ix_manual_publish_confirmations_state", "confirmation_state"),
        Index("ix_manual_publish_confirmations_platform_video_id", "target_platform", "actual_video_id"),
        Index("ix_manual_publish_confirmations_created_at", "created_at"),
    )


class UploadedVideo(Base):
    __tablename__ = "uploaded_videos"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id"), nullable=False
    )
    publish_handoff_package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("publish_handoff_packages.id"), nullable=False
    )
    manual_publish_confirmation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("manual_publish_confirmations.id"), nullable=False
    )
    render_package_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("render_package_snapshots.id"), nullable=False
    )
    source_manifest_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_manifest_snapshots.id")
    )
    rights_envelope_ref: Mapped[str | None] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    platform_video_id: Mapped[str] = mapped_column(Text, nullable=False)
    video_url: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    publish_status: Mapped[str] = mapped_column(String(40), nullable=False, default="CONFIRMED")
    actual_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    actual_disclosures: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    lineage_refs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    monitoring_state: Mapped[str] = mapped_column(String(40), nullable=False, default="NOT_STARTED")
    operator_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("channel_workspace_id", "platform", "platform_video_id", name="uq_uploaded_videos_channel_platform_video"),
        Index("ix_uploaded_videos_company_id", "company_id"),
        Index("ix_uploaded_videos_channel_workspace_id", "channel_workspace_id"),
        Index("ix_uploaded_videos_video_project_id", "video_project_id"),
        Index("ix_uploaded_videos_platform", "platform"),
        Index("ix_uploaded_videos_published_at", "published_at"),
        Index("ix_uploaded_videos_monitoring_state", "monitoring_state"),
    )


class UploadedVideoPublicationSummary(Base):
    __tablename__ = "uploaded_video_publication_summaries"

    id: Mapped[uuid.UUID] = uuid_pk()
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    platform_video_id: Mapped[str] = mapped_column(Text, nullable=False)
    video_url: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    publish_status: Mapped[str] = mapped_column(String(40), nullable=False)
    monitoring_state: Mapped[str] = mapped_column(String(40), nullable=False)
    operator_status: Mapped[str] = mapped_column(String(80), nullable=False)
    operator_summary: Mapped[str] = mapped_column(Text, nullable=False)
    next_action: Mapped[str | None] = mapped_column(Text)
    freshness_state: Mapped[str] = mapped_column(String(40), nullable=False, default="NOT_STARTED")
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("uploaded_video_id", name="uq_uploaded_video_publication_summaries_uploaded_video_id"),
        Index("ix_uploaded_video_publication_summaries_project_id", "video_project_id"),
        Index("ix_uploaded_video_publication_summaries_channel_id", "channel_workspace_id"),
        Index("ix_uploaded_video_publication_summaries_operator_status", "operator_status"),
        Index("ix_uploaded_video_publication_summaries_created_at", "created_at"),
    )
