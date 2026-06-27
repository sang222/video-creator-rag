import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class CloudMediaRef(Base):
    __tablename__ = "cloud_media_refs"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"))
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"))
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    uploaded_video_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"))
    render_package_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("render_package_snapshots.id"))
    media_type: Mapped[str] = mapped_column(String(60), nullable=False)
    storage_provider: Mapped[str] = mapped_column(String(40), nullable=False, default="GOOGLE_DRIVE")
    drive_file_id: Mapped[str] = mapped_column(Text, nullable=False)
    drive_folder_id: Mapped[str | None] = mapped_column(Text)
    web_view_link: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(Text)
    file_name: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum_sha256: Mapped[str | None] = mapped_column(String(128))
    local_source_path_hash: Mapped[str | None] = mapped_column(String(128))
    upload_status: Mapped[str] = mapped_column(String(40), nullable=False, default="VERIFIED")
    verification_status: Mapped[str] = mapped_column(String(40), nullable=False, default="SIZE_VERIFIED")
    local_cleanup_status: Mapped[str] = mapped_column(String(40), nullable=False, default="NOT_ELIGIBLE")
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleaned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retention_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    technical_appendix: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_cloud_media_refs_company", "company_id"),
        Index("ix_cloud_media_refs_channel", "channel_workspace_id"),
        Index("ix_cloud_media_refs_project", "video_project_id"),
        Index("ix_cloud_media_refs_uploaded_video", "uploaded_video_id"),
        Index("ix_cloud_media_refs_render_package", "render_package_id"),
        Index("ix_cloud_media_refs_media_type", "media_type"),
        Index("ix_cloud_media_refs_drive_file", "drive_file_id"),
        Index("ix_cloud_media_refs_upload_status", "upload_status"),
        Index("ix_cloud_media_refs_cleanup_status", "local_cleanup_status"),
    )


class MediaOffloadJob(Base):
    __tablename__ = "media_offload_jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"))
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"))
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    uploaded_video_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"))
    source_media_ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("final_media_refs.id"))
    render_package_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("render_package_snapshots.id"))
    local_source_path_hash: Mapped[str | None] = mapped_column(String(128))
    target_provider: Mapped[str] = mapped_column(String(40), nullable=False, default="GOOGLE_DRIVE")
    target_folder_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    target_media_type: Mapped[str] = mapped_column(String(60), nullable=False)
    job_state: Mapped[str] = mapped_column(String(40), nullable=False, default="PENDING")
    cloud_media_ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cloud_media_refs.id"))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(160))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_media_offload_jobs_company", "company_id"),
        Index("ix_media_offload_jobs_channel", "channel_workspace_id"),
        Index("ix_media_offload_jobs_project", "video_project_id"),
        Index("ix_media_offload_jobs_uploaded_video", "uploaded_video_id"),
        Index("ix_media_offload_jobs_source_media_ref", "source_media_ref_id"),
        Index("ix_media_offload_jobs_render_package", "render_package_id"),
        Index("ix_media_offload_jobs_cloud_ref", "cloud_media_ref_id"),
        Index("ix_media_offload_jobs_state", "job_state"),
        Index("ix_media_offload_jobs_created_at", "created_at"),
    )


class LocalMediaRetentionPolicy(Base):
    __tablename__ = "local_media_retention_policies"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"))
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"))
    keep_local_after_upload: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cleanup_after_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_local_age_hours: Mapped[int | None] = mapped_column(Integer)
    max_local_storage_gb: Mapped[int | None] = mapped_column(Integer)
    protected_paths: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    allowed_cleanup_roots: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_local_retention_policies_company", "company_id"),
        Index("ix_local_retention_policies_channel", "channel_workspace_id"),
        Index("ix_local_retention_policies_state", "state"),
    )


class GoogleDriveMediaCredential(Base):
    __tablename__ = "google_drive_media_credentials"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"))
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"))
    credential_reference_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credential_references.id"), nullable=False)
    connection_state: Mapped[str] = mapped_column(String(40), nullable=False, default="NOT_CONFIGURED")
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    root_folder_id: Mapped[str | None] = mapped_column(Text)
    last_health_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(160))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_google_drive_credentials_company", "company_id"),
        Index("ix_google_drive_credentials_channel", "channel_workspace_id"),
        Index("ix_google_drive_credentials_reference", "credential_reference_id"),
        Index("ix_google_drive_credentials_state", "connection_state"),
    )


class GoogleDriveOAuthSession(Base):
    __tablename__ = "google_drive_oauth_sessions"

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
        Index("ix_google_drive_oauth_sessions_company", "company_id"),
        Index("ix_google_drive_oauth_sessions_channel", "channel_workspace_id"),
        Index("ix_google_drive_oauth_sessions_state_hash", "state_token_hash"),
        Index("ix_google_drive_oauth_sessions_status", "status"),
        Index("ix_google_drive_oauth_sessions_created_at", "created_at"),
    )
