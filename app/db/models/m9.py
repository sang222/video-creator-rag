import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class PostPublishObservationWindow(Base):
    __tablename__ = "post_publish_observation_windows"

    id: Mapped[uuid.UUID] = uuid_pk()
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    platform_video_id: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observation_window: Mapped[str] = mapped_column(String(40), nullable=False)
    window_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expected_check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="PENDING")
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("uploaded_video_id", "observation_window", name="uq_post_publish_windows_video_window"),
        Index("ix_post_publish_windows_uploaded_video_id", "uploaded_video_id"),
        Index("ix_post_publish_windows_state", "state"),
        Index("ix_post_publish_windows_expected_check_at", "expected_check_at"),
        Index("ix_post_publish_windows_platform", "platform"),
    )


class DiagnosticTaxonomyVersion(Base):
    __tablename__ = "diagnostic_taxonomy_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    taxonomy_key: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    taxonomy_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("taxonomy_key", "version", name="uq_diagnostic_taxonomy_versions_key_version"),
        Index("ix_diagnostic_taxonomy_versions_key", "taxonomy_key"),
        Index("ix_diagnostic_taxonomy_versions_status", "status"),
    )


class PostPublishHealthRun(Base):
    __tablename__ = "post_publish_health_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
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
    observation_window: Mapped[str] = mapped_column(String(40), nullable=False)
    analytics_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("analytics_snapshots.id"))
    uploaded_video_metrics_summary_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("uploaded_video_metrics_summaries.id")
    )
    retention_curve_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("retention_curve_snapshots.id"))
    traffic_source_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("traffic_source_snapshots.id"))
    engagement_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("engagement_snapshots.id"))
    run_state: Mapped[str] = mapped_column(String(40), nullable=False, default="PENDING")
    health_state: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    severity: Mapped[str] = mapped_column(String(40), nullable=False, default="INFO")
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    operator_summary: Mapped[str] = mapped_column(Text, nullable=False)
    next_action: Mapped[str | None] = mapped_column(Text)
    do_not_do: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    technical_appendix: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_post_publish_health_runs_uploaded_video_id", "uploaded_video_id"),
        Index("ix_post_publish_health_runs_company_id", "company_id"),
        Index("ix_post_publish_health_runs_channel_workspace_id", "channel_workspace_id"),
        Index("ix_post_publish_health_runs_video_project_id", "video_project_id"),
        Index("ix_post_publish_health_runs_observation_window", "observation_window"),
        Index("ix_post_publish_health_runs_run_state", "run_state"),
        Index("ix_post_publish_health_runs_health_state", "health_state"),
        Index("ix_post_publish_health_runs_created_at", "created_at"),
    )


class NoViewDiagnosticRun(Base):
    __tablename__ = "no_view_diagnostic_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    post_publish_health_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("post_publish_health_runs.id"), nullable=False
    )
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False)
    analytics_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("analytics_snapshots.id"))
    uploaded_video_metrics_summary_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("uploaded_video_metrics_summaries.id")
    )
    observation_window: Mapped[str] = mapped_column(String(40), nullable=False)
    diagnostic_state: Mapped[str] = mapped_column(String(40), nullable=False)
    views: Mapped[float | None] = mapped_column(Float)
    impressions: Mapped[float | None] = mapped_column(Float)
    metric_availability: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    evidence_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    operator_summary: Mapped[str] = mapped_column(Text, nullable=False)
    next_action: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_no_view_diagnostic_runs_health_run_id", "post_publish_health_run_id"),
        Index("ix_no_view_diagnostic_runs_uploaded_video_id", "uploaded_video_id"),
        Index("ix_no_view_diagnostic_runs_state", "diagnostic_state"),
    )


class PackagingDiagnosticRun(Base):
    __tablename__ = "packaging_diagnostic_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    post_publish_health_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("post_publish_health_runs.id"), nullable=False
    )
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False)
    analytics_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("analytics_snapshots.id"))
    observation_window: Mapped[str] = mapped_column(String(40), nullable=False)
    diagnostic_state: Mapped[str] = mapped_column(String(40), nullable=False)
    impressions: Mapped[float | None] = mapped_column(Float)
    click_through_rate: Mapped[float | None] = mapped_column(Float)
    views: Mapped[float | None] = mapped_column(Float)
    evidence_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    operator_summary: Mapped[str] = mapped_column(Text, nullable=False)
    next_action: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_packaging_diagnostic_runs_health_run_id", "post_publish_health_run_id"),
        Index("ix_packaging_diagnostic_runs_uploaded_video_id", "uploaded_video_id"),
        Index("ix_packaging_diagnostic_runs_state", "diagnostic_state"),
    )


class RetentionDiagnosticRun(Base):
    __tablename__ = "retention_diagnostic_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    post_publish_health_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("post_publish_health_runs.id"), nullable=False
    )
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False)
    analytics_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("analytics_snapshots.id"))
    retention_curve_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("retention_curve_snapshots.id"))
    observation_window: Mapped[str] = mapped_column(String(40), nullable=False)
    diagnostic_state: Mapped[str] = mapped_column(String(40), nullable=False)
    average_view_duration_seconds: Mapped[float | None] = mapped_column(Float)
    average_view_percentage: Mapped[float | None] = mapped_column(Float)
    evidence_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    scene_alignment: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    operator_summary: Mapped[str] = mapped_column(Text, nullable=False)
    next_action: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_retention_diagnostic_runs_health_run_id", "post_publish_health_run_id"),
        Index("ix_retention_diagnostic_runs_uploaded_video_id", "uploaded_video_id"),
        Index("ix_retention_diagnostic_runs_state", "diagnostic_state"),
    )


class EngagementDiagnosticRun(Base):
    __tablename__ = "engagement_diagnostic_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    post_publish_health_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("post_publish_health_runs.id"), nullable=False
    )
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False)
    analytics_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("analytics_snapshots.id"))
    engagement_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("engagement_snapshots.id"))
    observation_window: Mapped[str] = mapped_column(String(40), nullable=False)
    diagnostic_state: Mapped[str] = mapped_column(String(40), nullable=False)
    engagement_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    evidence_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    operator_summary: Mapped[str] = mapped_column(Text, nullable=False)
    next_action: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_engagement_diagnostic_runs_health_run_id", "post_publish_health_run_id"),
        Index("ix_engagement_diagnostic_runs_uploaded_video_id", "uploaded_video_id"),
        Index("ix_engagement_diagnostic_runs_state", "diagnostic_state"),
    )


class PolicyRightsDiagnosticRun(Base):
    __tablename__ = "policy_rights_diagnostic_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    post_publish_health_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("post_publish_health_runs.id"), nullable=False
    )
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False)
    observation_window: Mapped[str] = mapped_column(String(40), nullable=False)
    diagnostic_state: Mapped[str] = mapped_column(String(40), nullable=False)
    source_manifest_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("source_manifest_snapshots.id"))
    rights_envelope_ref: Mapped[str | None] = mapped_column(Text)
    actual_disclosures: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    evidence_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    operator_summary: Mapped[str] = mapped_column(Text, nullable=False)
    next_action: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_policy_rights_diagnostic_runs_health_run_id", "post_publish_health_run_id"),
        Index("ix_policy_rights_diagnostic_runs_uploaded_video_id", "uploaded_video_id"),
        Index("ix_policy_rights_diagnostic_runs_state", "diagnostic_state"),
    )


class FailureTraceReport(Base):
    __tablename__ = "failure_trace_reports"

    id: Mapped[uuid.UUID] = uuid_pk()
    post_publish_health_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("post_publish_health_runs.id"), nullable=False
    )
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False)
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    platform_video_id: Mapped[str] = mapped_column(Text, nullable=False)
    observation_window: Mapped[str] = mapped_column(String(40), nullable=False)
    primary_status: Mapped[str] = mapped_column(String(40), nullable=False)
    primary_suspected_cause: Mapped[str | None] = mapped_column(String(120))
    secondary_suspected_causes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    severity: Mapped[str] = mapped_column(String(40), nullable=False, default="INFO")
    evidence_plain_text: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    operator_summary: Mapped[str] = mapped_column(Text, nullable=False)
    operator_report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    next_action: Mapped[str | None] = mapped_column(Text)
    do_not_do: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    technical_appendix: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_failure_trace_reports_health_run_id", "post_publish_health_run_id"),
        Index("ix_failure_trace_reports_uploaded_video_id", "uploaded_video_id"),
        Index("ix_failure_trace_reports_video_project_id", "video_project_id"),
        Index("ix_failure_trace_reports_primary_status", "primary_status"),
        Index("ix_failure_trace_reports_created_at", "created_at"),
    )


class RecoveryProposal(Base):
    __tablename__ = "recovery_proposals"

    id: Mapped[uuid.UUID] = uuid_pk()
    failure_trace_report_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("failure_trace_reports.id"), nullable=False)
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False)
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    proposal_type: Mapped[str] = mapped_column(String(60), nullable=False)
    proposal_state: Mapped[str] = mapped_column(String(40), nullable=False, default="PROPOSED")
    operator_summary: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_actions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    do_not_do: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    risk_level: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    requires_human_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_recovery_proposals_report_id", "failure_trace_report_id"),
        Index("ix_recovery_proposals_uploaded_video_id", "uploaded_video_id"),
        Index("ix_recovery_proposals_video_project_id", "video_project_id"),
        Index("ix_recovery_proposals_type", "proposal_type"),
        Index("ix_recovery_proposals_state", "proposal_state"),
        Index("ix_recovery_proposals_created_at", "created_at"),
    )
