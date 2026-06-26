import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class AnalyticsSyncRun(Base):
    __tablename__ = "analytics_sync_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False)
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    platform_video_id: Mapped[str] = mapped_column(Text, nullable=False)
    sync_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    sync_state: Mapped[str] = mapped_column(String(40), nullable=False, default="PENDING")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_key: Mapped[str | None] = mapped_column(String(160))
    provider_attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("provider_attempts.id"))
    analytics_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    next_action: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_analytics_sync_runs_company_id", "company_id"),
        Index("ix_analytics_sync_runs_channel_workspace_id", "channel_workspace_id"),
        Index("ix_analytics_sync_runs_uploaded_video_id", "uploaded_video_id"),
        Index("ix_analytics_sync_runs_video_project_id", "video_project_id"),
        Index("ix_analytics_sync_runs_state", "sync_state"),
        Index("ix_analytics_sync_runs_mode", "sync_mode"),
        Index("ix_analytics_sync_runs_platform", "platform"),
        Index("ix_analytics_sync_runs_created_at", "created_at"),
    )


class MetricDefinitionVersion(Base):
    __tablename__ = "metric_definition_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    metric_key: Mapped[str] = mapped_column(String(120), nullable=False)
    metric_name: Mapped[str] = mapped_column(Text, nullable=False)
    metric_group: Mapped[str] = mapped_column(String(40), nullable=False)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="ACTIVE")
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("metric_key", "platform", "version", name="uq_metric_definition_versions_key_platform_version"),
        Index("ix_metric_definition_versions_metric_key", "metric_key"),
        Index("ix_metric_definition_versions_platform", "platform"),
        Index("ix_metric_definition_versions_group", "metric_group"),
        Index("ix_metric_definition_versions_status", "status"),
    )


class MetricAvailabilitySnapshot(Base):
    __tablename__ = "metric_availability_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False)
    analytics_sync_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("analytics_sync_runs.id"))
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    platform_video_id: Mapped[str] = mapped_column(Text, nullable=False)
    availability_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    unavailable_metrics: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    unknown_metrics: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    source_metric_keys: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    freshness_state: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_metric_availability_snapshots_uploaded_video_id", "uploaded_video_id"),
        Index("ix_metric_availability_snapshots_sync_run_id", "analytics_sync_run_id"),
        Index("ix_metric_availability_snapshots_platform", "platform"),
        Index("ix_metric_availability_snapshots_captured_at", "captured_at"),
    )


class AnalyticsSnapshot(Base):
    __tablename__ = "analytics_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    analytics_sync_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("analytics_sync_runs.id"), nullable=False)
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    platform_video_id: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observation_window: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    metrics_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    normalized_metrics_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    metric_availability: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    freshness_state: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_analytics_snapshots_sync_run_id", "analytics_sync_run_id"),
        Index("ix_analytics_snapshots_uploaded_video_id", "uploaded_video_id"),
        Index("ix_analytics_snapshots_company_id", "company_id"),
        Index("ix_analytics_snapshots_channel_workspace_id", "channel_workspace_id"),
        Index("ix_analytics_snapshots_video_project_id", "video_project_id"),
        Index("ix_analytics_snapshots_platform", "platform"),
        Index("ix_analytics_snapshots_captured_at", "captured_at"),
        Index("ix_analytics_snapshots_created_at", "created_at"),
    )


class TrafficSourceSnapshot(Base):
    __tablename__ = "traffic_source_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    analytics_snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("analytics_snapshots.id"), nullable=False)
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    platform_video_id: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    traffic_sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    source_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    freshness_state: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_traffic_source_snapshots_analytics_snapshot_id", "analytics_snapshot_id"),
        Index("ix_traffic_source_snapshots_uploaded_video_id", "uploaded_video_id"),
        Index("ix_traffic_source_snapshots_captured_at", "captured_at"),
    )


class RetentionCurveSnapshot(Base):
    __tablename__ = "retention_curve_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    analytics_snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("analytics_snapshots.id"), nullable=False)
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False)
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    render_package_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("render_package_snapshots.id"))
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    platform_video_id: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    curve_points: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    curve_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    timeline_alignment: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    freshness_state: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_retention_curve_snapshots_analytics_snapshot_id", "analytics_snapshot_id"),
        Index("ix_retention_curve_snapshots_uploaded_video_id", "uploaded_video_id"),
        Index("ix_retention_curve_snapshots_video_project_id", "video_project_id"),
        Index("ix_retention_curve_snapshots_captured_at", "captured_at"),
    )


class EngagementSnapshot(Base):
    __tablename__ = "engagement_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    analytics_snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("analytics_snapshots.id"), nullable=False)
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    platform_video_id: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    engagement_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    freshness_state: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_engagement_snapshots_analytics_snapshot_id", "analytics_snapshot_id"),
        Index("ix_engagement_snapshots_uploaded_video_id", "uploaded_video_id"),
        Index("ix_engagement_snapshots_captured_at", "captured_at"),
    )


class UploadedVideoMetricsSummary(Base):
    __tablename__ = "uploaded_video_metrics_summaries"

    id: Mapped[uuid.UUID] = uuid_pk()
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    platform_video_id: Mapped[str] = mapped_column(Text, nullable=False)
    latest_analytics_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("analytics_snapshots.id"))
    latest_retention_curve_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("retention_curve_snapshots.id"))
    latest_traffic_source_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("traffic_source_snapshots.id"))
    latest_engagement_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("engagement_snapshots.id"))
    latest_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metrics_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    availability_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    freshness_state: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    monitoring_state: Mapped[str] = mapped_column(String(40), nullable=False, default="NO_DATA_YET")
    operator_summary: Mapped[str | None] = mapped_column(Text)
    next_action: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("uploaded_video_id", name="uq_uploaded_video_metrics_summaries_uploaded_video_id"),
        Index("ix_uploaded_video_metrics_summaries_company_id", "company_id"),
        Index("ix_uploaded_video_metrics_summaries_channel_id", "channel_workspace_id"),
        Index("ix_uploaded_video_metrics_summaries_project_id", "video_project_id"),
        Index("ix_uploaded_video_metrics_summaries_monitoring_state", "monitoring_state"),
        Index("ix_uploaded_video_metrics_summaries_latest_captured_at", "latest_captured_at"),
    )
