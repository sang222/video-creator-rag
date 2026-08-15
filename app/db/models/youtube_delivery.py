"""YouTube private staging and delivery authorities.

Private staging is deliberately separate from public publication.  A private
YouTube asset is remote review/storage custody only; it cannot create an
``UploadedVideo``, start analytics, or advance a series.  Only an immutable
``PublicPublicationReceipt`` may cross that boundary.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class YouTubePublishingCredential(Base):
    """Channel-bound write capability; secrets remain in CredentialReference."""

    __tablename__ = "youtube_publishing_credentials"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    credential_reference_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credential_references.id"), nullable=False
    )
    platform_channel_id: Mapped[str] = mapped_column(Text, nullable=False)
    account_identity: Mapped[str] = mapped_column(Text, nullable=False)
    oauth_scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    capabilities: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    public_release_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    delete_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "channel_workspace_id",
            "platform_channel_id",
            name="uq_youtube_publishing_credentials_channel_platform",
        ),
        UniqueConstraint(
            "credential_reference_id",
            name="uq_youtube_publishing_credentials_reference",
        ),
        CheckConstraint(
            "state in ('ACTIVE','REVOKED','BLOCKED')",
            name="ck_youtube_publishing_credentials_state",
        ),
        CheckConstraint(
            "public_release_allowed = false and delete_allowed = false",
            name="ck_youtube_publishing_credentials_private_only",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_youtube_publishing_credentials_hash",
        ),
        Index(
            "ix_youtube_publishing_credentials_company_channel",
            "company_id",
            "channel_workspace_id",
        ),
        Index("ix_youtube_publishing_credentials_state", "state"),
    )


class ProductionThumbnailBinding(Base):
    """Immutable selected AI-generated production thumbnail bytes."""

    __tablename__ = "production_thumbnail_bindings"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    final_review_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_review_candidates.id"), nullable=False
    )
    thumbnail_variant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("thumbnail_variants.id")
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_effect_ref: Mapped[str] = mapped_column(Text, nullable=False)
    provider_effect_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_ref: Mapped[str] = mapped_column(Text, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint(
            "final_review_candidate_id",
            name="uq_production_thumbnail_bindings_candidate",
        ),
        UniqueConstraint(
            "checksum_sha256", name="uq_production_thumbnail_bindings_checksum"
        ),
        CheckConstraint(
            "source_type = 'AI_GENERATED' and state = 'VERIFIED'",
            name="ck_production_thumbnail_bindings_authority",
        ),
        CheckConstraint(
            "mime_type in ('image/jpeg','image/png') and size_bytes > 0 and "
            "size_bytes <= 2097152 and width > 0 and height > 0",
            name="ck_production_thumbnail_bindings_media",
        ),
        CheckConstraint(
            "provider_effect_hash ~ '^[0-9a-f]{64}$' and "
            "checksum_sha256 ~ '^[0-9a-f]{64}$' and "
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_production_thumbnail_bindings_hashes",
        ),
        Index("ix_production_thumbnail_bindings_project", "video_project_id"),
    )


class YouTubePrivateStage(Base):
    """Mutable projection for one exact private YouTube review asset."""

    __tablename__ = "youtube_private_stages"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    final_review_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_review_candidates.id"), nullable=False
    )
    final_video_decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_video_decisions.id"), nullable=False
    )
    final_media_ref_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_media_refs.id"), nullable=False
    )
    final_media_ref: Mapped[str] = mapped_column(Text, nullable=False)
    final_media_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    publishing_credential_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("youtube_publishing_credentials.id"), nullable=False
    )
    production_thumbnail_binding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_thumbnail_bindings.id"),
        nullable=False,
    )
    caption_ref: Mapped[str] = mapped_column(Text, nullable=False)
    caption_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    staging_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    staging_metadata_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    public_release_expectation: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    public_release_expectation_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    state: Mapped[str] = mapped_column(String(48), nullable=False, default="PREPARED")
    platform_video_id: Mapped[str | None] = mapped_column(Text)
    studio_url: Mapped[str | None] = mapped_column(Text)
    observed_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    observed_metadata_hash: Mapped[str | None] = mapped_column(String(64))
    processing_status: Mapped[str | None] = mapped_column(String(48))
    private_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_error_code: Mapped[str | None] = mapped_column(String(160))
    identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "final_video_decision_id", name="uq_youtube_private_stages_decision"
        ),
        UniqueConstraint("identity_hash", name="uq_youtube_private_stages_identity"),
        UniqueConstraint(
            "publishing_credential_id",
            "platform_video_id",
            name="uq_youtube_private_stages_platform_video",
        ),
        CheckConstraint(
            "state in ('PREPARED','SESSION_CREATED','UPLOADING','OUTCOME_UNKNOWN',"
            "'BYTES_ACCEPTED','PROCESSING','PRIVATE_VERIFIED','BLOCKED','FAILED')",
            name="ck_youtube_private_stages_state",
        ),
        CheckConstraint(
            "final_media_checksum ~ '^[0-9a-f]{64}$' and "
            "caption_hash ~ '^[0-9a-f]{64}$' and "
            "staging_metadata_hash ~ '^[0-9a-f]{64}$' and "
            "public_release_expectation_hash ~ '^[0-9a-f]{64}$' and "
            "identity_hash ~ '^[0-9a-f]{64}$' and "
            "(observed_metadata_hash is null or observed_metadata_hash ~ '^[0-9a-f]{64}$')",
            name="ck_youtube_private_stages_hashes",
        ),
        CheckConstraint(
            "coalesce(staging_metadata->>'privacy_status','') = 'PRIVATE' and "
            "coalesce(staging_metadata->>'public_release_by_api','false') = 'false' and "
            "coalesce(public_release_expectation->>'manual_release_only','false') = 'true'",
            name="ck_youtube_private_stages_private_only",
        ),
        CheckConstraint(
            "(state <> 'PRIVATE_VERIFIED') or (platform_video_id is not null and "
            "studio_url is not null and observed_metadata is not null and "
            "observed_metadata_hash is not null and processing_status = 'SUCCEEDED' and "
            "private_verified_at is not null)",
            name="ck_youtube_private_stages_verified",
        ),
        Index("ix_youtube_private_stages_project", "video_project_id"),
        Index("ix_youtube_private_stages_state", "state"),
    )


class YouTubeUploadAttempt(Base):
    """One resumable upload session; a second videos.insert is forbidden."""

    __tablename__ = "youtube_upload_attempts"

    id: Mapped[uuid.UUID] = uuid_pk()
    youtube_private_stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("youtube_private_stages.id"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    provider_effect_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    session_secret_ref: Mapped[str | None] = mapped_column(Text)
    session_uri_hash: Mapped[str | None] = mapped_column(String(64))
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    committed_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="INTENDED")
    outcome_certainty: Mapped[str] = mapped_column(
        String(32), nullable=False, default="NOT_SUBMITTED"
    )
    provider_video_id: Mapped[str | None] = mapped_column(Text)
    provider_response_hash: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "youtube_private_stage_id",
            name="uq_youtube_upload_attempts_stage",
        ),
        UniqueConstraint(
            "provider_effect_key", name="uq_youtube_upload_attempts_effect_key"
        ),
        CheckConstraint(
            "attempt_number = 1 and total_bytes > 0 and committed_bytes >= 0 and "
            "committed_bytes <= total_bytes",
            name="ck_youtube_upload_attempts_counts",
        ),
        CheckConstraint(
            "state in ('INTENDED','SESSION_SUBMITTED','SESSION_CREATED','UPLOADING','OUTCOME_UNKNOWN',"
            "'BYTES_ACCEPTED','VERIFIED','FAILED')",
            name="ck_youtube_upload_attempts_state",
        ),
        CheckConstraint(
            "outcome_certainty in ('NOT_SUBMITTED','CERTAIN_PENDING','UNCERTAIN','CERTAIN_SUCCESS','CERTAIN_FAILURE')",
            name="ck_youtube_upload_attempts_certainty",
        ),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$' and "
            "(session_uri_hash is null or session_uri_hash ~ '^[0-9a-f]{64}$') and "
            "(provider_response_hash is null or provider_response_hash ~ '^[0-9a-f]{64}$')",
            name="ck_youtube_upload_attempts_hashes",
        ),
        CheckConstraint(
            "(state not in ('SESSION_CREATED','UPLOADING','BYTES_ACCEPTED','VERIFIED')) or "
            "(session_secret_ref is not null and session_uri_hash is not null)",
            name="ck_youtube_upload_attempts_session",
        ),
        CheckConstraint(
            "(state <> 'VERIFIED') or (provider_video_id is not null and "
            "provider_response_hash is not null and committed_bytes = total_bytes and "
            "outcome_certainty = 'CERTAIN_SUCCESS')",
            name="ck_youtube_upload_attempts_verified",
        ),
        Index("ix_youtube_upload_attempts_state", "state"),
    )


class YouTubeComponentAttempt(Base):
    """At-most-once thumbnail/caption write effect."""

    __tablename__ = "youtube_component_attempts"

    id: Mapped[uuid.UUID] = uuid_pk()
    youtube_private_stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("youtube_private_stages.id"), nullable=False
    )
    component_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_effect_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="INTENDED")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_resource_id: Mapped[str | None] = mapped_column(Text)
    response_hash: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "youtube_private_stage_id",
            "component_type",
            name="uq_youtube_component_attempts_stage_component",
        ),
        UniqueConstraint(
            "provider_effect_key", name="uq_youtube_component_attempts_effect"
        ),
        CheckConstraint(
            "component_type in ('THUMBNAIL','CAPTION')",
            name="ck_youtube_component_attempts_type",
        ),
        CheckConstraint(
            "state in ('INTENDED','SUBMITTED','OUTCOME_UNKNOWN','VERIFIED','FAILED')",
            name="ck_youtube_component_attempts_state",
        ),
        CheckConstraint(
            "attempt_count between 0 and 1",
            name="ck_youtube_component_attempts_count",
        ),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$' and "
            "(response_hash is null or response_hash ~ '^[0-9a-f]{64}$')",
            name="ck_youtube_component_attempts_hashes",
        ),
        CheckConstraint(
            "(state <> 'VERIFIED') or (attempt_count = 1 and provider_resource_id is not null "
            "and response_hash is not null)",
            name="ck_youtube_component_attempts_verified",
        ),
    )


class YouTubeComponentReceipt(Base):
    """Immutable verification receipt for post-upload components/readbacks."""

    __tablename__ = "youtube_component_receipts"

    id: Mapped[uuid.UUID] = uuid_pk()
    youtube_private_stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("youtube_private_stages.id"), nullable=False
    )
    component_type: Mapped[str] = mapped_column(String(48), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_resource_id: Mapped[str | None] = mapped_column(Text)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint(
            "youtube_private_stage_id",
            "component_type",
            name="uq_youtube_component_receipts_stage_component",
        ),
        CheckConstraint(
            "component_type in ('VIDEO_UPLOAD','THUMBNAIL','CAPTION','METADATA_READBACK','PROCESSING_READBACK')",
            name="ck_youtube_component_receipts_type",
        ),
        CheckConstraint(
            "state = 'VERIFIED'",
            name="ck_youtube_component_receipts_state",
        ),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$' and response_hash ~ '^[0-9a-f]{64}$' "
            "and receipt_hash ~ '^[0-9a-f]{64}$'",
            name="ck_youtube_component_receipts_hashes",
        ),
        Index("ix_youtube_component_receipts_stage", "youtube_private_stage_id"),
    )


class PublicPublicationReceipt(Base):
    """Immutable proof that an exact staged/manual asset became PUBLIC."""

    __tablename__ = "public_publication_receipts"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    final_review_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_review_candidates.id"), nullable=False
    )
    final_video_decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_video_decisions.id"), nullable=False
    )
    manual_publish_confirmation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("manual_publish_confirmations.id"), nullable=False
    )
    youtube_private_stage_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("youtube_private_stages.id")
    )
    platform_channel_id: Mapped[str] = mapped_column(Text, nullable=False)
    platform_video_id: Mapped[str] = mapped_column(Text, nullable=False)
    public_url: Mapped[str] = mapped_column(Text, nullable=False)
    observed_privacy_status: Mapped[str] = mapped_column(String(20), nullable=False)
    observed_published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    observed_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    observed_metadata_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    verification_evidence_ref: Mapped[str] = mapped_column(Text, nullable=False)
    verification_evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint(
            "final_video_decision_id",
            name="uq_public_publication_receipts_decision",
        ),
        UniqueConstraint(
            "platform_channel_id",
            "platform_video_id",
            name="uq_public_publication_receipts_platform_video",
        ),
        UniqueConstraint(
            "manual_publish_confirmation_id",
            name="uq_public_publication_receipts_confirmation",
        ),
        CheckConstraint(
            "observed_privacy_status = 'PUBLIC'",
            name="ck_public_publication_receipts_public",
        ),
        CheckConstraint(
            "observed_metadata_hash ~ '^[0-9a-f]{64}$' and "
            "verification_evidence_hash ~ '^[0-9a-f]{64}$' and "
            "receipt_hash ~ '^[0-9a-f]{64}$'",
            name="ck_public_publication_receipts_hashes",
        ),
        Index("ix_public_publication_receipts_project", "video_project_id"),
        Index("ix_public_publication_receipts_published", "observed_published_at"),
    )


class LocalMediaPurgeAttempt(Base):
    """Crash-reconcilable local purge command sealed before filesystem mutation."""

    __tablename__ = "local_media_purge_attempts"

    id: Mapped[uuid.UUID] = uuid_pk()
    youtube_private_stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("youtube_private_stages.id"), nullable=False
    )
    final_media_ref_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_media_refs.id"), nullable=False
    )
    command_id: Mapped[str] = mapped_column(String(160), nullable=False)
    original_file_ref: Mapped[str] = mapped_column(Text, nullable=False)
    quarantine_file_ref: Mapped[str] = mapped_column(Text, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="INTENDED")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(160))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "youtube_private_stage_id", name="uq_local_media_purge_attempts_stage"
        ),
        UniqueConstraint("command_id", name="uq_local_media_purge_attempts_command"),
        CheckConstraint(
            "state in ('INTENDED','SUBMITTED','QUARANTINED','PURGED','BLOCKED')",
            name="ck_local_media_purge_attempts_state",
        ),
        CheckConstraint(
            "attempt_count between 0 and 1 and "
            "((state = 'INTENDED' and attempt_count = 0) or "
            "(state in ('SUBMITTED','QUARANTINED','PURGED','BLOCKED') and attempt_count = 1))",
            name="ck_local_media_purge_attempts_count",
        ),
        CheckConstraint(
            "checksum_sha256 ~ '^[0-9a-f]{64}$' and "
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_local_media_purge_attempts_hashes",
        ),
        Index("ix_local_media_purge_attempts_state", "state"),
    )


class LocalMediaPurgeReceipt(Base):
    """Exact local-MP4 deletion after remote private verification."""

    __tablename__ = "local_media_purge_receipts"

    id: Mapped[uuid.UUID] = uuid_pk()
    youtube_private_stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("youtube_private_stages.id"), nullable=False
    )
    final_media_ref_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_media_refs.id"), nullable=False
    )
    local_file_ref: Mapped[str] = mapped_column(Text, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint(
            "youtube_private_stage_id", name="uq_local_media_purge_receipts_stage"
        ),
        UniqueConstraint(
            "final_media_ref_id", name="uq_local_media_purge_receipts_final_media"
        ),
        CheckConstraint("state = 'PURGED'", name="ck_local_media_purge_receipts_state"),
        CheckConstraint(
            "checksum_sha256 ~ '^[0-9a-f]{64}$' and receipt_hash ~ '^[0-9a-f]{64}$'",
            name="ck_local_media_purge_receipts_hashes",
        ),
    )


class TelegramDeliveryNotification(Base):
    """At-most-once operator notification with no bot secret persisted."""

    __tablename__ = "telegram_delivery_notifications"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    final_review_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_review_candidates.id"), nullable=False
    )
    credential_reference_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credential_references.id")
    )
    chat_binding_ref: Mapped[str | None] = mapped_column(Text)
    notification_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_message_id: Mapped[str | None] = mapped_column(Text)
    provider_response_hash: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(160))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "final_review_candidate_id",
            "notification_kind",
            name="uq_telegram_delivery_notifications_candidate_kind",
        ),
        CheckConstraint(
            "notification_kind in ('FINAL_REVIEW_READY','YOUTUBE_PRIVATE_VERIFIED')",
            name="ck_telegram_delivery_notifications_kind",
        ),
        CheckConstraint(
            "state in ('PENDING','SUBMITTED','OUTCOME_UNKNOWN','SENT','BLOCKED_CONFIG','FAILED')",
            name="ck_telegram_delivery_notifications_state",
        ),
        CheckConstraint(
            "attempt_count between 0 and 1",
            name="ck_telegram_delivery_notifications_attempts",
        ),
        CheckConstraint(
            "payload_hash ~ '^[0-9a-f]{64}$' and "
            "(provider_response_hash is null or provider_response_hash ~ '^[0-9a-f]{64}$')",
            name="ck_telegram_delivery_notifications_hashes",
        ),
        CheckConstraint(
            "(state <> 'SENT') or (attempt_count = 1 and provider_message_id is not null "
            "and provider_response_hash is not null and sent_at is not null)",
            name="ck_telegram_delivery_notifications_sent",
        ),
        Index("ix_telegram_delivery_notifications_state", "state"),
    )


class YouTubeSeriesPlaylistBinding(Base):
    """Exact external playlist identity for a VCOS SeriesPlan."""

    __tablename__ = "youtube_series_playlist_bindings"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    series_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_plans.id"), nullable=False
    )
    publishing_credential_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("youtube_publishing_credentials.id"), nullable=False
    )
    platform_channel_id: Mapped[str] = mapped_column(Text, nullable=False)
    youtube_playlist_id: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(48), nullable=False)
    expected_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    observed_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    binding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "series_plan_id", name="uq_youtube_series_playlist_bindings_series"
        ),
        UniqueConstraint(
            "platform_channel_id",
            "youtube_playlist_id",
            name="uq_youtube_series_playlist_bindings_playlist",
        ),
        CheckConstraint(
            "state in ('NOT_CREATED','CREATE_PENDING','CREATED_UNVERIFIED','VERIFIED_EMPTY',"
            "'VERIFIED_WITH_EPISODES','SYNC_DRIFT','BLOCKED')",
            name="ck_youtube_series_playlist_bindings_state",
        ),
        CheckConstraint(
            "binding_hash ~ '^[0-9a-f]{64}$'",
            name="ck_youtube_series_playlist_bindings_hash",
        ),
        Index("ix_youtube_series_playlist_bindings_state", "state"),
    )


class YouTubeSeriesEpisodeBinding(Base):
    """Exact episode/video/playlist identity; no count-based inference."""

    __tablename__ = "youtube_series_episode_bindings"

    id: Mapped[uuid.UUID] = uuid_pk()
    youtube_series_playlist_binding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("youtube_series_playlist_bindings.id"),
        nullable=False,
    )
    series_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_plans.id"), nullable=False
    )
    series_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_runs.id"), nullable=False
    )
    technical_episode_number: Mapped[int] = mapped_column(Integer, nullable=False)
    public_episode_ordinal: Mapped[int | None] = mapped_column(Integer)
    public_ordinal_authority_ref: Mapped[str | None] = mapped_column(Text)
    public_ordinal_authority_hash: Mapped[str | None] = mapped_column(String(64))
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    youtube_private_stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("youtube_private_stages.id"), nullable=False
    )
    public_publication_receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("public_publication_receipts.id")
    )
    youtube_video_id: Mapped[str] = mapped_column(Text, nullable=False)
    youtube_playlist_item_id: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(48), nullable=False)
    expected_position: Mapped[int | None] = mapped_column(Integer)
    actual_position: Mapped[int | None] = mapped_column(Integer)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    binding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "series_run_id",
            "technical_episode_number",
            name="uq_youtube_series_episode_bindings_run_episode",
        ),
        UniqueConstraint(
            "youtube_private_stage_id",
            name="uq_youtube_series_episode_bindings_stage",
        ),
        UniqueConstraint(
            "youtube_playlist_item_id",
            name="uq_youtube_series_episode_bindings_playlist_item",
        ),
        CheckConstraint(
            "technical_episode_number > 0 and "
            "(public_episode_ordinal is null or public_episode_ordinal > 0) and "
            "(expected_position is null or expected_position >= 0) and "
            "(actual_position is null or actual_position >= 0)",
            name="ck_youtube_series_episode_bindings_ordinals",
        ),
        CheckConstraint(
            "state in ('PRIVATE_UPLOADED','PUBLICATION_VERIFIED','PLAYLIST_BIND_PENDING',"
            "'PLAYLIST_BOUND_UNVERIFIED','PLAYLIST_BOUND_VERIFIED','SYNC_DRIFT','BLOCKED')",
            name="ck_youtube_series_episode_bindings_state",
        ),
        CheckConstraint(
            "binding_hash ~ '^[0-9a-f]{64}$' and "
            "(public_ordinal_authority_hash is null or public_ordinal_authority_hash ~ '^[0-9a-f]{64}$')",
            name="ck_youtube_series_episode_bindings_hashes",
        ),
        CheckConstraint(
            "(state not in ('PLAYLIST_BIND_PENDING','PLAYLIST_BOUND_UNVERIFIED','PLAYLIST_BOUND_VERIFIED')) or "
            "(public_episode_ordinal is not null and public_ordinal_authority_ref is not null "
            "and public_ordinal_authority_hash is not null and expected_position is not null)",
            name="ck_youtube_series_episode_bindings_public_ordinal",
        ),
        Index("ix_youtube_series_episode_bindings_series", "series_plan_id"),
        Index("ix_youtube_series_episode_bindings_state", "state"),
    )


def _immutable_delivery_authority(
    _mapper: Mapper[Any], _connection: Any, target: Any
) -> None:
    raise RuntimeError(f"{target.__class__.__name__.upper()}_IMMUTABLE")


for _model in (
    ProductionThumbnailBinding,
    YouTubeComponentReceipt,
    PublicPublicationReceipt,
    LocalMediaPurgeReceipt,
):
    event.listen(_model, "before_update", _immutable_delivery_authority)
    event.listen(_model, "before_delete", _immutable_delivery_authority)
