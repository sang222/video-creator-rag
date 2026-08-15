import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class PublishHandoffPackage(Base):
    __tablename__ = "publish_handoff_packages"

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
    production_package_artifact_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_versions.id")
    )
    production_package_hash: Mapped[str | None] = mapped_column(String(64))
    duration_contract: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("compiled_channel_policy_snapshots.id"),
        nullable=False,
    )
    production_artifact_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_artifact_runs.id")
    )
    render_package_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("render_package_snapshots.id"), nullable=False
    )
    render_spec_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("render_spec_snapshots.id")
    )
    media_qc_report_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_qc_reports.id")
    )
    accessibility_qc_report_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accessibility_qc_reports.id")
    )
    source_manifest_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_manifest_snapshots.id")
    )
    asset_manifest_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("asset_manifest_snapshots.id")
    )
    target_platform: Mapped[str] = mapped_column(String(40), nullable=False)
    target_surface: Mapped[str] = mapped_column(String(40), nullable=False)
    destination_binding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    destination_binding_fingerprint: Mapped[str | None] = mapped_column(String(64))
    market_policy_hash: Mapped[str | None] = mapped_column(String(64))
    approved_package_hash: Mapped[str | None] = mapped_column(String(64))
    approval_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approval_decisions.id")
    )
    target_market_profile_ref: Mapped[str | None] = mapped_column(Text)
    target_market_profile_hash: Mapped[str | None] = mapped_column(String(64))
    market_alignment_dossier_ref: Mapped[str | None] = mapped_column(Text)
    market_alignment_dossier_hash: Mapped[str | None] = mapped_column(String(64))
    approved_publish_timezone: Mapped[str | None] = mapped_column(Text)
    approved_publish_window: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    render_variant_id: Mapped[str | None] = mapped_column(String(120))
    package_state: Mapped[str] = mapped_column(
        String(40), nullable=False, default="DRAFT"
    )
    planned_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    planned_disclosures: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    planned_files: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    cloud_media_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    checklist_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    operator_instructions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    risk_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    next_action: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        CheckConstraint(
            "(production_package_artifact_version_id is null "
            "and production_package_hash is null "
            "and duration_contract is null) or "
            "(production_package_artifact_version_id is not null "
            "and production_package_hash ~ '^[0-9a-f]{64}$' "
            "and duration_contract is not null "
            "and jsonb_typeof(duration_contract) = 'object')",
            name="ck_publish_handoff_packages_production_package_binding",
        ),
        Index("ix_publish_handoff_packages_company_id", "company_id"),
        Index(
            "ix_publish_handoff_packages_channel_workspace_id", "channel_workspace_id"
        ),
        Index("ix_publish_handoff_packages_video_project_id", "video_project_id"),
        Index(
            "ix_publish_handoff_packages_production_package",
            "production_package_artifact_version_id",
        ),
        Index(
            "ix_publish_handoff_packages_render_package_id",
            "render_package_snapshot_id",
        ),
        Index("ix_publish_handoff_packages_state", "package_state"),
        Index("ix_publish_handoff_packages_platform", "target_platform"),
        Index("ix_publish_handoff_packages_created_at", "created_at"),
    )


class ManualPublishConfirmation(Base):
    __tablename__ = "manual_publish_confirmations"

    id: Mapped[uuid.UUID] = uuid_pk()
    publish_handoff_package_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("publish_handoff_packages.id")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("compiled_channel_policy_snapshots.id"),
        nullable=False,
    )
    target_platform: Mapped[str] = mapped_column(String(40), nullable=False)
    target_surface: Mapped[str] = mapped_column(String(40), nullable=False)
    confirmed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    confirmation_state: Mapped[str] = mapped_column(
        String(40), nullable=False, default="DRAFT"
    )
    actual_video_id: Mapped[str | None] = mapped_column(Text)
    actual_video_url: Mapped[str | None] = mapped_column(Text)
    actual_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    destination_binding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    destination_binding_fingerprint: Mapped[str | None] = mapped_column(String(64))
    market_policy_hash: Mapped[str | None] = mapped_column(String(64))
    approved_package_hash: Mapped[str | None] = mapped_column(String(64))
    actual_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    actual_disclosures: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    actual_files: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    operator_notes: Mapped[str | None] = mapped_column(Text)
    validation_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    metadata_diff: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    next_action: Mapped[str | None] = mapped_column(Text)
    schema_version: Mapped[str] = mapped_column(
        String(16), nullable=False, default="v1"
    )
    command_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    confirmation_hash: Mapped[str | None] = mapped_column(String(64))
    human_upload_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("human_upload_tasks.id")
    )
    final_review_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_review_candidates.id")
    )
    final_video_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_video_decisions.id")
    )
    final_media_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_media_refs.id")
    )
    reviewed_checksum: Mapped[str | None] = mapped_column(String(64))
    production_package_artifact_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_versions.id")
    )
    production_package_hash: Mapped[str | None] = mapped_column(String(64))
    channel_profile_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_profile_versions.id")
    )
    platform_channel_id: Mapped[str | None] = mapped_column(Text)
    destination_account_identity: Mapped[str | None] = mapped_column(Text)
    actual_duration_seconds: Mapped[Any | None] = mapped_column(Numeric(18, 6))
    thumbnail_confirmed: Mapped[bool | None] = mapped_column(Boolean)
    caption_confirmed: Mapped[bool | None] = mapped_column(Boolean)
    playlist_id: Mapped[str | None] = mapped_column(Text)
    playlist_order: Mapped[int | None] = mapped_column(Integer)
    materiality_policy_hash: Mapped[str | None] = mapped_column(String(64))
    variance_attested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    variance_attested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    corrected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correction_history: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    verified_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_command_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True)
    )
    verification_evidence_ref: Mapped[str | None] = mapped_column(Text)
    verification_evidence_hash: Mapped[str | None] = mapped_column(String(64))
    canceled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "command_id",
            name="uq_manual_publish_confirmations_command_id",
        ),
        UniqueConstraint(
            "human_upload_task_id",
            name="uq_manual_publish_confirmations_human_upload_task_id",
        ),
        UniqueConstraint(
            "final_video_decision_id",
            name="uq_manual_publish_confirmations_final_video_decision_id",
        ),
        UniqueConstraint(
            "verification_command_id",
            name="uq_manual_publish_confirmations_verification_command_id",
        ),
        CheckConstraint(
            "schema_version in ('v1','v2','v3')",
            name="ck_manual_publish_confirmations_schema_version",
        ),
        CheckConstraint(
            "confirmation_state in "
            "('DRAFT','SUBMITTED','ACCEPTED','REVIEW_REQUIRED','REJECTED',"
            "'CANCELLED','VERIFIED','REJECTED_MISMATCH',"
            "'BLOCKED_DESTINATION','CORRECTION_REQUIRED',"
            "'VARIANCE_ACCEPTED','CANCELED')",
            name="ck_manual_publish_confirmations_state",
        ),
        CheckConstraint(
            "(schema_version = 'v1' and publish_handoff_package_id is not null) "
            "or (schema_version = 'v2' "
            "and publish_handoff_package_id is null "
            "and human_upload_task_id is not null "
            "and final_review_candidate_id is not null "
            "and final_video_decision_id is not null "
            "and final_media_ref_id is not null "
            "and reviewed_checksum ~ '^[0-9a-f]{64}$' "
            "and production_package_artifact_version_id is not null "
            "and production_package_hash ~ '^[0-9a-f]{64}$' "
            "and channel_profile_version_id is not null "
            "and destination_binding_id is not null "
            "and destination_binding_fingerprint ~ '^[0-9a-f]{64}$' "
            "and platform_channel_id is not null "
            "and destination_account_identity is not null "
            "and confirmed_by_user_id is not null "
            "and command_id is not null "
            "and confirmation_hash ~ '^[0-9a-f]{64}$' "
            "and actual_video_id is not null "
            "and actual_video_url is not null "
            "and actual_published_at is not null "
            "and actual_duration_seconds > 0 "
            "and thumbnail_confirmed is not null "
            "and caption_confirmed is not null "
            "and materiality_policy_hash ~ '^[0-9a-f]{64}$' "
            "and confirmation_state in "
            "('SUBMITTED','VERIFIED','REJECTED_MISMATCH',"
            "'BLOCKED_DESTINATION','CORRECTION_REQUIRED',"
            "'VARIANCE_ACCEPTED','CANCELED'))",
            name="ck_manual_publish_confirmations_v2_binding",
        ),
        CheckConstraint(
            "(schema_version = 'v1') or "
            "(confirmation_state <> 'VARIANCE_ACCEPTED') or "
            "(variance_attested_by_user_id is not null "
            "and variance_attested_at is not null)",
            name="ck_manual_publish_confirmations_v2_variance_attestation",
        ),
        CheckConstraint(
            "(schema_version = 'v1') or "
            "(confirmation_state <> 'VERIFIED') or "
            "(verified_by_user_id is not null "
            "and verified_at is not null "
            "and verification_command_id is not null "
            "and verification_evidence_ref is not null "
            "and verification_evidence_hash ~ '^[0-9a-f]{64}$')",
            name="ck_manual_publish_confirmations_v2_verified",
        ),
        Index(
            "ix_manual_publish_confirmations_handoff_id", "publish_handoff_package_id"
        ),
        Index(
            "ix_manual_publish_confirmations_channel_workspace_id",
            "channel_workspace_id",
        ),
        Index("ix_manual_publish_confirmations_video_project_id", "video_project_id"),
        Index(
            "ix_manual_publish_confirmations_final_media_ref_id",
            "final_media_ref_id",
        ),
        Index("ix_manual_publish_confirmations_state", "confirmation_state"),
        Index(
            "ix_manual_publish_confirmations_platform_video_id",
            "target_platform",
            "actual_video_id",
        ),
        Index("ix_manual_publish_confirmations_created_at", "created_at"),
    )


class UploadedVideo(Base):
    __tablename__ = "uploaded_videos"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id")
    )
    policy_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id")
    )
    publish_handoff_package_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("publish_handoff_packages.id")
    )
    manual_publish_confirmation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("manual_publish_confirmations.id")
    )
    render_package_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("render_package_snapshots.id")
    )
    first_scripted_video_package_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("first_scripted_video_packages.id")
    )
    human_upload_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("human_upload_tasks.id")
    )
    destination: Mapped[str] = mapped_column(
        String(40), nullable=False, default="YOUTUBE"
    )
    destination_binding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    destination_binding_fingerprint: Mapped[str | None] = mapped_column(String(64))
    market_policy_hash: Mapped[str | None] = mapped_column(String(64))
    approved_package_hash: Mapped[str | None] = mapped_column(String(64))
    source_manifest_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_manifest_snapshots.id")
    )
    rights_envelope_ref: Mapped[str | None] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    platform_video_id: Mapped[str] = mapped_column(Text, nullable=False)
    video_url: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    publish_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="CONFIRMED"
    )
    actual_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    actual_disclosures: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    lineage_refs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    monitoring_state: Mapped[str] = mapped_column(
        String(40), nullable=False, default="NOT_STARTED"
    )
    operator_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    actual_title: Mapped[str | None] = mapped_column(Text)
    actual_visibility: Mapped[str] = mapped_column(
        String(40), nullable=False, default="UNKNOWN"
    )
    actual_publish_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    actual_upload_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    playlist_id: Mapped[str | None] = mapped_column(Text)
    thumbnail_uploaded: Mapped[bool | None] = mapped_column(Boolean)
    subtitles_uploaded: Mapped[bool | None] = mapped_column(Boolean)
    description_modified_from_package: Mapped[bool | None] = mapped_column(Boolean)
    package_metadata_diff: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    verification_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="NOT_VERIFIED"
    )
    analytics_sync_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="NOT_STARTED"
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_analytics_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    operator_note: Mapped[str | None] = mapped_column(Text)
    schema_version: Mapped[str] = mapped_column(
        String(16), nullable=False, default="v1"
    )
    final_review_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_review_candidates.id")
    )
    final_video_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_video_decisions.id")
    )
    final_media_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_media_refs.id")
    )
    production_package_artifact_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_versions.id")
    )
    production_package_hash: Mapped[str | None] = mapped_column(String(64))
    channel_profile_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_profile_versions.id")
    )
    reviewed_checksum: Mapped[str | None] = mapped_column(String(64))
    production_lane: Mapped[str | None] = mapped_column(String(40))
    content_mode: Mapped[str | None] = mapped_column(String(40))
    series_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_plans.id")
    )
    series_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_runs.id")
    )
    episode_number: Mapped[int | None] = mapped_column(Integer)
    standalone_reason_code: Mapped[str | None] = mapped_column(String(160))
    target_market_lineage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    archive_supplement: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    archive_supplement_ref: Mapped[str | None] = mapped_column(Text)
    archive_supplement_hash: Mapped[str | None] = mapped_column(String(64))
    verified_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domain_events.id")
    )
    analytics_ready_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domain_events.id")
    )
    analytics_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    public_publication_receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("public_publication_receipts.id")
    )
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "channel_workspace_id",
            "platform",
            "platform_video_id",
            name="uq_uploaded_videos_channel_platform_video",
        ),
        UniqueConstraint(
            "manual_publish_confirmation_id",
            name="uq_uploaded_videos_manual_publish_confirmation_id",
        ),
        UniqueConstraint(
            "final_video_decision_id",
            name="uq_uploaded_videos_final_video_decision_id",
        ),
        UniqueConstraint(
            "final_media_ref_id",
            name="uq_uploaded_videos_final_media_ref_id",
        ),
        UniqueConstraint(
            "verified_event_id",
            name="uq_uploaded_videos_verified_event_id",
        ),
        UniqueConstraint(
            "analytics_ready_event_id",
            name="uq_uploaded_videos_analytics_ready_event_id",
        ),
        UniqueConstraint(
            "public_publication_receipt_id",
            name="uq_uploaded_videos_public_receipt",
        ),
        CheckConstraint(
            "schema_version in ('v1','v2','v3')",
            name="ck_uploaded_videos_schema_version",
        ),
        CheckConstraint(
            "(schema_version <> 'v3') or "
            "(public_publication_receipt_id is not null "
            "and actual_visibility = 'PUBLIC' "
            "and verification_status = 'VERIFIED' "
            "and analytics_sync_status = 'READY')",
            name="ck_uploaded_videos_v3_public_receipt",
        ),
        CheckConstraint(
            "(schema_version = 'v1') or "
            "(video_project_id is not null "
            "and policy_snapshot_id is not null "
            "and manual_publish_confirmation_id is not null "
            "and human_upload_task_id is not null "
            "and final_review_candidate_id is not null "
            "and final_video_decision_id is not null "
            "and final_media_ref_id is not null "
            "and production_package_artifact_version_id is not null "
            "and production_package_hash ~ '^[0-9a-f]{64}$' "
            "and channel_profile_version_id is not null "
            "and reviewed_checksum ~ '^[0-9a-f]{64}$' "
            "and destination_binding_id is not null "
            "and destination_binding_fingerprint ~ '^[0-9a-f]{64}$' "
            "and production_lane = 'LONG_FORM' "
            "and content_mode in ('SERIES_EPISODE','STANDALONE') "
            "and target_market_lineage is not null "
            "and archive_supplement is not null "
            "and archive_supplement_ref is not null "
            "and archive_supplement_hash ~ '^[0-9a-f]{64}$' "
            "and verification_status = 'VERIFIED' "
            "and analytics_sync_status = 'READY' "
            "and verified_event_id is not null "
            "and analytics_ready_event_id is not null "
            "and analytics_ready_at is not null)",
            name="ck_uploaded_videos_v2_binding",
        ),
        CheckConstraint(
            "(schema_version = 'v1') or "
            "((content_mode = 'SERIES_EPISODE' "
            "and series_plan_id is not null "
            "and series_run_id is not null "
            "and episode_number > 0 "
            "and standalone_reason_code is null) "
            "or (content_mode = 'STANDALONE' "
            "and series_plan_id is null "
            "and series_run_id is null "
            "and episode_number is null "
            "and standalone_reason_code is not null))",
            name="ck_uploaded_videos_v2_assignment",
        ),
        Index("ix_uploaded_videos_company_id", "company_id"),
        Index("ix_uploaded_videos_channel_workspace_id", "channel_workspace_id"),
        Index("ix_uploaded_videos_video_project_id", "video_project_id"),
        Index("ix_uploaded_videos_first_package_id", "first_scripted_video_package_id"),
        Index("ix_uploaded_videos_human_upload_task_id", "human_upload_task_id"),
        Index("ix_uploaded_videos_final_media_ref_id", "final_media_ref_id"),
        Index(
            "ix_uploaded_videos_production_package",
            "production_package_artifact_version_id",
        ),
        Index("ix_uploaded_videos_series_run_id", "series_run_id"),
        Index("ix_uploaded_videos_destination", "destination"),
        Index("ix_uploaded_videos_platform", "platform"),
        Index("ix_uploaded_videos_published_at", "published_at"),
        Index("ix_uploaded_videos_monitoring_state", "monitoring_state"),
        Index("ix_uploaded_videos_verification_status", "verification_status"),
        Index("ix_uploaded_videos_analytics_sync_status", "analytics_sync_status"),
    )


class UploadedVideoPublicationSummary(Base):
    __tablename__ = "uploaded_video_publication_summaries"

    id: Mapped[uuid.UUID] = uuid_pk()
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    platform_video_id: Mapped[str] = mapped_column(Text, nullable=False)
    video_url: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    publish_status: Mapped[str] = mapped_column(String(40), nullable=False)
    monitoring_state: Mapped[str] = mapped_column(String(40), nullable=False)
    operator_status: Mapped[str] = mapped_column(String(80), nullable=False)
    operator_summary: Mapped[str] = mapped_column(Text, nullable=False)
    next_action: Mapped[str | None] = mapped_column(Text)
    freshness_state: Mapped[str] = mapped_column(
        String(40), nullable=False, default="NOT_STARTED"
    )
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "uploaded_video_id",
            name="uq_uploaded_video_publication_summaries_uploaded_video_id",
        ),
        Index("ix_uploaded_video_publication_summaries_project_id", "video_project_id"),
        Index(
            "ix_uploaded_video_publication_summaries_channel_id", "channel_workspace_id"
        ),
        Index(
            "ix_uploaded_video_publication_summaries_operator_status", "operator_status"
        ),
        Index("ix_uploaded_video_publication_summaries_created_at", "created_at"),
    )
