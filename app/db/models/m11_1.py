import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class OperatorUser(Base):
    __tablename__ = "operator_users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("email", name="uq_operator_users_email"),
        Index("ix_operator_users_email", "email"),
        Index("ix_operator_users_role", "role"),
        Index("ix_operator_users_status", "status"),
    )


class OperatorAuthSession(Base):
    __tablename__ = "operator_auth_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("operator_users.id"), nullable=False)
    session_token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("session_token_hash", name="uq_operator_auth_sessions_token_hash"),
        Index("ix_operator_auth_sessions_user", "user_id"),
        Index("ix_operator_auth_sessions_expires_at", "expires_at"),
        Index("ix_operator_auth_sessions_revoked_at", "revoked_at"),
    )


class LocalizedSubtitlePackage(Base):
    __tablename__ = "localized_subtitle_packages"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False)
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    base_caption_track_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("caption_track_snapshots.id"))
    source_language: Mapped[str] = mapped_column(String(16), nullable=False)
    target_language: Mapped[str] = mapped_column(String(16), nullable=False)
    srt_cloud_media_ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cloud_media_refs.id"))
    vtt_cloud_media_ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cloud_media_refs.id"))
    translation_status: Mapped[str] = mapped_column(String(40), nullable=False)
    human_review_status: Mapped[str] = mapped_column(String(40), nullable=False)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("operator_users.id"))
    quality_notes: Mapped[str | None] = mapped_column(Text)
    disclosure_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_localized_subtitle_packages_company", "company_id"),
        Index("ix_localized_subtitle_packages_channel", "channel_workspace_id"),
        Index("ix_localized_subtitle_packages_project", "video_project_id"),
        Index("ix_localized_subtitle_packages_language", "target_language"),
        Index("ix_localized_subtitle_packages_translation", "translation_status"),
        Index("ix_localized_subtitle_packages_review", "human_review_status"),
    )


class LocalizedMetadataPackage(Base):
    __tablename__ = "localized_metadata_packages"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False)
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    region: Mapped[str | None] = mapped_column(String(16))
    localized_title: Mapped[str] = mapped_column(Text, nullable=False)
    localized_description: Mapped[str] = mapped_column(Text, nullable=False)
    localized_tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    localized_disclosure_notes: Mapped[str | None] = mapped_column(Text)
    localized_cta_text: Mapped[str | None] = mapped_column(Text)
    human_review_status: Mapped[str] = mapped_column(String(40), nullable=False)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("operator_users.id"))
    quality_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_localized_metadata_packages_company", "company_id"),
        Index("ix_localized_metadata_packages_channel", "channel_workspace_id"),
        Index("ix_localized_metadata_packages_project", "video_project_id"),
        Index("ix_localized_metadata_packages_language", "language"),
        Index("ix_localized_metadata_packages_review", "human_review_status"),
    )


class ChannelPublishTimingPolicy(Base):
    __tablename__ = "channel_publish_timing_policies"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False)
    primary_timezone: Mapped[str] = mapped_column(Text, nullable=False)
    operator_timezone: Mapped[str | None] = mapped_column(Text)
    target_regions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    primary_audience_country: Mapped[str | None] = mapped_column(String(16))
    preferred_publish_windows: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    avoid_publish_windows: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    publish_days: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    weekend_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("channel_workspace_id", name="uq_channel_publish_timing_policies_channel"),
        Index("ix_channel_publish_timing_policies_channel", "channel_workspace_id"),
        Index("ix_channel_publish_timing_policies_timezone", "primary_timezone"),
    )


class PublishTimingSuggestion(Base):
    __tablename__ = "publish_timing_suggestions"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False)
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    publish_handoff_package_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("publish_handoff_packages.id"))
    target_timezone: Mapped[str] = mapped_column(Text, nullable=False)
    operator_timezone: Mapped[str | None] = mapped_column(Text)
    suggested_publish_at_local: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    suggested_publish_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    operator_local_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="CHANNEL_CONFIG")
    confidence_label: Mapped[str] = mapped_column(String(40), nullable=False, default="CONFIGURED")
    operator_summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_publish_timing_suggestions_channel", "channel_workspace_id"),
        Index("ix_publish_timing_suggestions_project", "video_project_id"),
        Index("ix_publish_timing_suggestions_handoff", "publish_handoff_package_id"),
        Index("ix_publish_timing_suggestions_utc", "suggested_publish_at_utc"),
    )
