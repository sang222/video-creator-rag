import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class YouTubeMonitoringCredential(Base):
    __tablename__ = "youtube_monitoring_credentials"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"))
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"))
    credential_reference_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credential_references.id"), nullable=False
    )
    auth_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(80), nullable=False)
    connection_state: Mapped[str] = mapped_column(String(40), nullable=False, default="NOT_CONFIGURED")
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    token_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_health_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(160))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_youtube_monitoring_credentials_company", "company_id"),
        Index("ix_youtube_monitoring_credentials_channel", "channel_workspace_id"),
        Index("ix_youtube_monitoring_credentials_reference", "credential_reference_id"),
        Index("ix_youtube_monitoring_credentials_provider", "provider_key"),
        Index("ix_youtube_monitoring_credentials_state", "connection_state"),
    )


class YouTubeOAuthSession(Base):
    __tablename__ = "youtube_oauth_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"))
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"))
    state_token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="STARTED")
    credential_reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("credential_references.id"))
    error_code: Mapped[str | None] = mapped_column(String(160))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_youtube_oauth_sessions_company", "company_id"),
        Index("ix_youtube_oauth_sessions_channel", "channel_workspace_id"),
        Index("ix_youtube_oauth_sessions_state_hash", "state_token_hash"),
        Index("ix_youtube_oauth_sessions_status", "status"),
        Index("ix_youtube_oauth_sessions_created_at", "created_at"),
    )


class UploadedVideoYouTubePublicMonitorSnapshot(Base):
    __tablename__ = "uploaded_video_youtube_public_monitor_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"))
    platform_video_id: Mapped[str] = mapped_column(Text, nullable=False)
    video_url: Mapped[str | None] = mapped_column(Text)
    views: Mapped[int | None] = mapped_column(BigInteger)
    likes: Mapped[int | None] = mapped_column(BigInteger)
    comments: Mapped[int | None] = mapped_column(BigInteger)
    youtube_title: Mapped[str | None] = mapped_column(Text)
    youtube_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    youtube_channel_id: Mapped[str | None] = mapped_column(Text)
    youtube_channel_title: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    duration_seconds: Mapped[int | None] = mapped_column(BigInteger)
    definition: Mapped[str | None] = mapped_column(String(40))
    caption_status: Mapped[str | None] = mapped_column(String(40))
    privacy_status: Mapped[str | None] = mapped_column(String(40))
    public_stats_viewable: Mapped[bool | None] = mapped_column(Boolean)
    title_matches_confirmed_metadata: Mapped[bool | None] = mapped_column(Boolean)
    duration_matches_render_package: Mapped[bool | None] = mapped_column(Boolean)
    views_availability: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    likes_availability: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    comments_availability: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    freshness_state: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    sync_status: Mapped[str] = mapped_column(String(40), nullable=False, default="OK")
    sync_error_code: Mapped[str | None] = mapped_column(String(160))
    learning_authority: Mapped[str] = mapped_column(String(40), nullable=False, default="WEAK")
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    unknown_metrics: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    unavailable_metrics: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    technical_appendix: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_youtube_public_snapshots_uploaded_video", "uploaded_video_id"),
        Index("ix_youtube_public_snapshots_company", "company_id"),
        Index("ix_youtube_public_snapshots_channel", "channel_workspace_id"),
        Index("ix_youtube_public_snapshots_platform_video", "platform_video_id"),
        Index("ix_youtube_public_snapshots_last_synced", "last_synced_at"),
        Index("ix_youtube_public_snapshots_status", "sync_status"),
    )


class UploadedVideoYouTubeOwnerAnalyticsSnapshot(Base):
    __tablename__ = "uploaded_video_youtube_owner_analytics_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"))
    platform_video_id: Mapped[str] = mapped_column(Text, nullable=False)
    analytics_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    analytics_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    learning_authority: Mapped[str] = mapped_column(String(40), nullable=False, default="STRONG")
    views: Mapped[int | None] = mapped_column(BigInteger)
    likes: Mapped[int | None] = mapped_column(BigInteger)
    comments: Mapped[int | None] = mapped_column(BigInteger)
    impressions: Mapped[int | None] = mapped_column(BigInteger)
    impression_click_through_rate: Mapped[float | None] = mapped_column(Float)
    average_view_duration_seconds: Mapped[float | None] = mapped_column(Float)
    average_view_percentage: Mapped[float | None] = mapped_column(Float)
    estimated_minutes_watched: Mapped[float | None] = mapped_column(Float)
    subscribers_gained: Mapped[int | None] = mapped_column(BigInteger)
    subscribers_lost: Mapped[int | None] = mapped_column(BigInteger)
    metric_availability: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    freshness_state: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    sync_status: Mapped[str] = mapped_column(String(40), nullable=False, default="OK")
    sync_error_code: Mapped[str | None] = mapped_column(String(160))
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    technical_appendix: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_youtube_owner_snapshots_uploaded_video", "uploaded_video_id"),
        Index("ix_youtube_owner_snapshots_company", "company_id"),
        Index("ix_youtube_owner_snapshots_channel", "channel_workspace_id"),
        Index("ix_youtube_owner_snapshots_platform_video", "platform_video_id"),
        Index("ix_youtube_owner_snapshots_last_synced", "last_synced_at"),
        Index("ix_youtube_owner_snapshots_status", "sync_status"),
    )


class YouTubePublicSyncRun(Base):
    __tablename__ = "youtube_public_sync_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"))
    platform_video_id: Mapped[str] = mapped_column(Text, nullable=False)
    run_state: Mapped[str] = mapped_column(String(40), nullable=False, default="PENDING")
    source: Mapped[str] = mapped_column(String(80), nullable=False, default="YOUTUBE_DATA_API")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(160))
    error_message: Mapped[str | None] = mapped_column(Text)
    metrics_found: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("uploaded_video_youtube_public_monitor_snapshots.id")
    )
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_youtube_public_sync_runs_uploaded_video", "uploaded_video_id"),
        Index("ix_youtube_public_sync_runs_company", "company_id"),
        Index("ix_youtube_public_sync_runs_channel", "channel_workspace_id"),
        Index("ix_youtube_public_sync_runs_platform_video", "platform_video_id"),
        Index("ix_youtube_public_sync_runs_state", "run_state"),
        Index("ix_youtube_public_sync_runs_created_at", "created_at"),
    )


class YouTubeOwnerAnalyticsSyncRun(Base):
    __tablename__ = "youtube_owner_analytics_sync_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"))
    platform_video_id: Mapped[str] = mapped_column(Text, nullable=False)
    credential_reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("credential_references.id"))
    run_state: Mapped[str] = mapped_column(String(40), nullable=False, default="PENDING")
    source: Mapped[str] = mapped_column(String(80), nullable=False, default="YOUTUBE_ANALYTICS_API")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(160))
    error_message: Mapped[str | None] = mapped_column(Text)
    metrics_found: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("uploaded_video_youtube_owner_analytics_snapshots.id")
    )
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_youtube_owner_sync_runs_uploaded_video", "uploaded_video_id"),
        Index("ix_youtube_owner_sync_runs_company", "company_id"),
        Index("ix_youtube_owner_sync_runs_channel", "channel_workspace_id"),
        Index("ix_youtube_owner_sync_runs_platform_video", "platform_video_id"),
        Index("ix_youtube_owner_sync_runs_state", "run_state"),
        Index("ix_youtube_owner_sync_runs_created_at", "created_at"),
    )
