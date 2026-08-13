"""Durable exactly-once journal for package-authorized V2 production effects."""

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
    inspect,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


V2_EFFECT_STATES = (
    "PREPARED",
    "EFFECT_STARTED",
    "VERIFIED",
    "FAILED_UNCERTAIN",
)
V2_EFFECT_STAGES = ("MEDIA", "RENDER", "QC", "ARCHIVE")


class V2ProductionEffectLedger(Base):
    """One durable logical side effect for one workflow stage command."""

    __tablename__ = "v2_production_effect_ledger"

    id: Mapped[uuid.UUID] = uuid_pk()
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_workflow_runs.id"),
        nullable=False,
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("video_projects.id"),
        nullable=False,
    )
    production_package_artifact_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("artifact_versions.id"),
        nullable=False,
    )
    production_package_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    command_id: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    adapter_key: Mapped[str] = mapped_column(String(80), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="PREPARED")
    effect_invocation_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    result_type: Mapped[str | None] = mapped_column(String(120))
    result_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    result_ref: Mapped[str | None] = mapped_column(Text)
    result_hash: Mapped[str | None] = mapped_column(String(64))
    result_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    authority_refs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    effect_journal: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id",
            "stage",
            name="uq_v2_production_effect_ledger_run_stage",
        ),
        CheckConstraint(
            "stage in (" + ",".join(f"'{stage}'" for stage in V2_EFFECT_STAGES) + ")",
            name="ck_v2_production_effect_ledger_stage",
        ),
        CheckConstraint(
            "state in (" + ",".join(f"'{state}'" for state in V2_EFFECT_STATES) + ")",
            name="ck_v2_production_effect_ledger_state",
        ),
        CheckConstraint(
            "production_package_hash ~ '^[0-9a-f]{64}$' "
            "and input_hash ~ '^[0-9a-f]{64}$'",
            name="ck_v2_production_effect_ledger_identity_hashes",
        ),
        CheckConstraint(
            "result_hash is null or result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_v2_production_effect_ledger_result_hash",
        ),
        CheckConstraint(
            "effect_invocation_count between 0 and 1",
            name="ck_v2_production_effect_ledger_invocation_count",
        ),
        CheckConstraint(
            "(state = 'PREPARED' and effect_invocation_count = 0 "
            "and started_at is null and completed_at is null) or "
            "(state in ('EFFECT_STARTED','FAILED_UNCERTAIN') "
            "and effect_invocation_count = 1 and started_at is not null "
            "and completed_at is null) or "
            "(state = 'VERIFIED' and effect_invocation_count = 1 "
            "and started_at is not null and completed_at is not null "
            "and result_type is not null and result_hash is not null)",
            name="ck_v2_production_effect_ledger_state_evidence",
        ),
        CheckConstraint(
            "completed_at is null or completed_at >= started_at",
            name="ck_v2_production_effect_ledger_completion_order",
        ),
        Index(
            "ix_v2_production_effect_ledger_project",
            "video_project_id",
        ),
        Index(
            "ix_v2_production_effect_ledger_package",
            "production_package_artifact_version_id",
        ),
        Index(
            "ix_v2_production_effect_ledger_state",
            "state",
            "updated_at",
        ),
        Index(
            "ix_v2_production_effect_ledger_operation",
            "adapter_key",
            "operation_id",
        ),
    )


class V2NarrationTimingRecoveryAuthority(Base):
    """Immutable authority to recover timing without repeating final TTS."""

    __tablename__ = "v2_narration_timing_recovery_authorities"

    id: Mapped[uuid.UUID] = uuid_pk()
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_workflow_runs.id"),
        nullable=False,
        unique=True,
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    media_effect_ledger_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("v2_production_effect_ledger.id"),
        nullable=False,
        unique=True,
    )
    media_domain_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("domain_events.id"),
        nullable=False,
        unique=True,
    )
    media_dead_letter_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dead_letter_jobs.id"),
        nullable=False,
        unique=True,
    )
    root_replacement_authority_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("script_contract_replacement_authorities.id"),
        nullable=False,
        unique=True,
    )
    verifier_settlement_authority_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("controlled_verifier_settlement_authorities.id"),
        nullable=False,
        unique=True,
    )
    settlement_qualification_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("script_qualification_runs.id"),
        nullable=False,
        unique=True,
    )
    production_package_artifact_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_versions.id"), nullable=False
    )
    production_package_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    script_artifact_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_versions.id"), nullable=False
    )
    script_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_script_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    budget_reservation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mr1_monthly_budget_reservations.id"),
        nullable=False,
        unique=True,
    )
    budget_reservation_ref: Mapped[str] = mapped_column(Text, nullable=False)
    budget_authority_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    tts_request_journal_ref: Mapped[str] = mapped_column(Text, nullable=False)
    tts_request_identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    tts_idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)
    audio_relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    audio_checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    audio_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    audio_duration_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_failure_reason_code: Mapped[str] = mapped_column(
        String(160), nullable=False
    )
    forced_alignment_permission_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False
    )
    max_tts_retries: Mapped[int] = mapped_column(Integer, nullable=False)
    max_forced_alignment_submissions: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    recovery_reason: Mapped[str] = mapped_column(String(160), nullable=False)
    authorized_by_actor_type: Mapped[str] = mapped_column(String(80), nullable=False)
    authorized_by_actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    authorized_by_actor_role: Mapped[str] = mapped_column(String(80), nullable=False)
    authority_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        CheckConstraint(
            "schema_version = "
            "'vcos.v2-narration-timing-recovery-authority.v1' and "
            "recovery_reason = "
            "'DURABLE_TTS_AUDIO_MISSING_TIMING_PROVENANCE' and "
            "original_failure_reason_code = 'V2_ELEVENLABS_PROVIDER_FAILURE'",
            name="ck_v2_narration_timing_recovery_authority_identity",
        ),
        CheckConstraint(
            "forced_alignment_permission_confirmed and max_tts_retries = 0 "
            "and max_forced_alignment_submissions = 1 and audio_size_bytes > 0 "
            "and audio_duration_ms > 0",
            name="ck_v2_narration_timing_recovery_authority_bounds",
        ),
        CheckConstraint(
            "production_package_hash ~ '^[0-9a-f]{64}$' and "
            "script_content_hash ~ '^[0-9a-f]{64}$' and "
            "approved_script_hash ~ '^[0-9a-f]{64}$' and "
            "budget_authority_hash ~ '^[0-9a-f]{64}$' and "
            "provider_policy_hash ~ '^[0-9a-f]{64}$' and "
            "tts_request_identity_hash ~ '^[0-9a-f]{64}$' and "
            "audio_checksum_sha256 ~ '^[0-9a-f]{64}$' and "
            "authority_hash ~ '^[0-9a-f]{64}$'",
            name="ck_v2_narration_timing_recovery_authority_hashes",
        ),
        Index(
            "ix_v2_narration_timing_recovery_authority_project",
            "video_project_id",
        ),
        Index(
            "ix_v2_narration_timing_recovery_authority_created_at",
            "created_at",
        ),
    )


class V2NarrationTimingRecoveryReceipt(Base):
    """Immutable proof that one forced-alignment recovery verified MEDIA."""

    __tablename__ = "v2_narration_timing_recovery_receipts"

    id: Mapped[uuid.UUID] = uuid_pk()
    authority_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("v2_narration_timing_recovery_authorities.id"),
        nullable=False,
        unique=True,
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_workflow_runs.id"),
        nullable=False,
        unique=True,
    )
    media_effect_ledger_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("v2_production_effect_ledger.id"),
        nullable=False,
        unique=True,
    )
    forced_alignment_request_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    forced_alignment_provider_response_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    forced_alignment_provider_request_id: Mapped[str | None] = mapped_column(
        String(200)
    )
    forced_alignment_provider_request_id_availability: Mapped[str] = mapped_column(
        String(40), nullable=False
    )
    forced_alignment_evidence_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    recovered_timing_seed_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    narration_receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_media_timeline_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    provider_call_count: Mapped[int] = mapped_column(Integer, nullable=False)
    tts_retry_count: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    recovery_state: Mapped[str] = mapped_column(String(40), nullable=False)
    receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        CheckConstraint(
            "schema_version = "
            "'vcos.v2-narration-timing-recovery-receipt.v1' and "
            "recovery_state = 'VERIFIED' and provider_call_count = 1 and "
            "tts_retry_count = 0",
            name="ck_v2_narration_timing_recovery_receipt_identity",
        ),
        CheckConstraint(
            "forced_alignment_provider_request_id_availability in "
            "('PRESENT','NOT_EXPOSED_BY_ENDPOINT') and "
            "((forced_alignment_provider_request_id_availability = 'PRESENT' "
            "and forced_alignment_provider_request_id is not null) or "
            "(forced_alignment_provider_request_id_availability = "
            "'NOT_EXPOSED_BY_ENDPOINT' and "
            "forced_alignment_provider_request_id is null))",
            name="ck_v2_narration_timing_recovery_receipt_request_id",
        ),
        CheckConstraint(
            "forced_alignment_request_hash ~ '^[0-9a-f]{64}$' and "
            "forced_alignment_provider_response_hash ~ '^[0-9a-f]{64}$' and "
            "forced_alignment_evidence_hash ~ '^[0-9a-f]{64}$' and "
            "recovered_timing_seed_hash ~ '^[0-9a-f]{64}$' and "
            "narration_receipt_hash ~ '^[0-9a-f]{64}$' and "
            "canonical_media_timeline_hash ~ '^[0-9a-f]{64}$' and "
            "receipt_hash ~ '^[0-9a-f]{64}$'",
            name="ck_v2_narration_timing_recovery_receipt_hashes",
        ),
        Index("ix_v2_narration_timing_recovery_receipt_created_at", "created_at"),
    )


class V2DriveArchivePropertyLimitRecoveryAuthority(Base):
    """Immutable authority for one exact Drive app-property repair upload."""

    __tablename__ = "v2_drive_archive_property_limit_recovery_authorities"

    id: Mapped[uuid.UUID] = uuid_pk()
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "production_workflow_runs.id",
            name="fk_v2_drive_archive_recovery_workflow",
        ),
        nullable=False,
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("video_projects.id", name="fk_v2_drive_archive_recovery_project"),
        nullable=False,
    )
    archive_effect_ledger_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "v2_production_effect_ledger.id",
            name="fk_v2_drive_archive_recovery_effect",
        ),
        nullable=False,
    )
    archive_domain_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("domain_events.id", name="fk_v2_drive_archive_recovery_event"),
        nullable=False,
    )
    archive_dead_letter_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "dead_letter_jobs.id", name="fk_v2_drive_archive_recovery_dead_letter"
        ),
        nullable=False,
    )
    root_replacement_authority_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "script_contract_replacement_authorities.id",
            name="fk_v2_drive_archive_recovery_root",
        ),
        nullable=False,
    )
    verifier_settlement_authority_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "controlled_verifier_settlement_authorities.id",
            name="fk_v2_drive_archive_recovery_settlement",
        ),
        nullable=False,
    )
    settlement_qualification_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "script_qualification_runs.id",
            name="fk_v2_drive_archive_recovery_qualification",
        ),
        nullable=False,
    )
    production_package_artifact_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("artifact_versions.id", name="fk_v2_drive_archive_recovery_package"),
        nullable=False,
    )
    production_package_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    render_output_ref: Mapped[str] = mapped_column(Text, nullable=False)
    render_output_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    caption_output_ref: Mapped[str] = mapped_column(Text, nullable=False)
    caption_output_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    technical_qc_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    creative_qc_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cross_modal_qc_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    budget_reservation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "mr1_monthly_budget_reservations.id",
            name="fk_v2_drive_archive_recovery_budget",
        ),
        nullable=False,
    )
    budget_reservation_ref: Mapped[str] = mapped_column(Text, nullable=False)
    budget_authority_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    drive_credential_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "google_drive_media_credentials.id",
            name="fk_v2_drive_archive_recovery_credential",
        ),
        nullable=False,
    )
    drive_root_folder_id: Mapped[str] = mapped_column(Text, nullable=False)
    media_folder_path: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    caption_folder_path: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    archive_command_id: Mapped[str] = mapped_column(String(160), nullable=False)
    archive_operation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    archive_adapter_key: Mapped[str] = mapped_column(String(80), nullable=False)
    archive_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    legacy_request_journal_ref: Mapped[str] = mapped_column(Text, nullable=False)
    legacy_request_journal_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    legacy_media_idempotency_key: Mapped[str] = mapped_column(
        String(240), nullable=False
    )
    legacy_caption_idempotency_key: Mapped[str] = mapped_column(
        String(240), nullable=False
    )
    media_idempotency_key: Mapped[str] = mapped_column(String(124), nullable=False)
    caption_idempotency_key: Mapped[str] = mapped_column(String(124), nullable=False)
    absence_reconciliation_evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    absence_reconciliation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    original_failure_reason_code: Mapped[str] = mapped_column(
        String(160), nullable=False
    )
    defect_code: Mapped[str] = mapped_column(String(160), nullable=False)
    max_actual_upload_submissions: Mapped[int] = mapped_column(Integer, nullable=False)
    automatic_publish: Mapped[bool] = mapped_column(Boolean, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    recovery_reason: Mapped[str] = mapped_column(String(160), nullable=False)
    authorized_by_actor_type: Mapped[str] = mapped_column(String(80), nullable=False)
    authorized_by_actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    authorized_by_actor_role: Mapped[str] = mapped_column(String(80), nullable=False)
    authority_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        CheckConstraint(
            "schema_version = 'vcos.v2-drive-archive-property-limit-recovery-authority.v1' "
            "and recovery_reason = 'DRIVE_APP_PROPERTY_LIMIT_PRE_FILE_FAILURE' "
            "and original_failure_reason_code = 'V2_GOOGLE_DRIVE_ARCHIVE_PROVIDER_FAILURE' "
            "and defect_code = 'GOOGLE_DRIVE_APP_PROPERTY_VALUE_LIMIT_EXCEEDED' "
            "and archive_adapter_key = 'v2-google-drive-remote'",
            name="ck_v2_drive_archive_recovery_authority_identity",
        ),
        CheckConstraint(
            "max_actual_upload_submissions = 1 and not automatic_publish "
            "and octet_length('vcos_idempotency_key') + "
            "octet_length(media_idempotency_key) between 21 and 124 "
            "and octet_length('vcos_idempotency_key') + "
            "octet_length(caption_idempotency_key) between 21 and 124 "
            "and media_idempotency_key <> caption_idempotency_key "
            "and legacy_media_idempotency_key <> '' "
            "and legacy_caption_idempotency_key <> '' "
            "and render_output_ref <> '' and caption_output_ref <> '' "
            "and legacy_request_journal_ref <> '' "
            "and jsonb_typeof(media_folder_path) = 'array' "
            "and jsonb_array_length(media_folder_path) > 0 "
            "and jsonb_typeof(caption_folder_path) = 'array' "
            "and jsonb_array_length(caption_folder_path) > 0",
            name="ck_v2_drive_archive_recovery_authority_bounds",
        ),
        CheckConstraint(
            "production_package_hash ~ '^[0-9a-f]{64}$' and "
            "render_output_checksum ~ '^[0-9a-f]{64}$' and "
            "caption_output_checksum ~ '^[0-9a-f]{64}$' and "
            "technical_qc_hash ~ '^[0-9a-f]{64}$' and "
            "creative_qc_hash ~ '^[0-9a-f]{64}$' and "
            "cross_modal_qc_hash ~ '^[0-9a-f]{64}$' and "
            "budget_authority_hash ~ '^[0-9a-f]{64}$' and "
            "archive_input_hash ~ '^[0-9a-f]{64}$' and "
            "legacy_request_journal_hash ~ '^[0-9a-f]{64}$' and "
            "absence_reconciliation_hash ~ '^[0-9a-f]{64}$' and "
            "authority_hash ~ '^[0-9a-f]{64}$'",
            name="ck_v2_drive_archive_recovery_authority_hashes",
        ),
        Index("ix_v2_drive_archive_recovery_authority_project", "video_project_id"),
        Index("ix_v2_drive_archive_recovery_authority_created_at", "created_at"),
        UniqueConstraint(
            "workflow_run_id", name="uq_v2_drive_archive_recovery_workflow"
        ),
        UniqueConstraint(
            "archive_effect_ledger_id", name="uq_v2_drive_archive_recovery_effect"
        ),
        UniqueConstraint(
            "archive_domain_event_id", name="uq_v2_drive_archive_recovery_event"
        ),
        UniqueConstraint(
            "archive_dead_letter_job_id",
            name="uq_v2_drive_archive_recovery_dead_letter",
        ),
        UniqueConstraint(
            "root_replacement_authority_id",
            name="uq_v2_drive_archive_recovery_root",
        ),
        UniqueConstraint(
            "verifier_settlement_authority_id",
            name="uq_v2_drive_archive_recovery_settlement",
        ),
        UniqueConstraint(
            "settlement_qualification_run_id",
            name="uq_v2_drive_archive_recovery_qualification",
        ),
        UniqueConstraint(
            "budget_reservation_id",
            name="uq_v2_drive_archive_recovery_budget",
        ),
        UniqueConstraint("authority_hash", name="uq_v2_drive_archive_recovery_hash"),
    )


class V2DriveArchivePropertyLimitRecoveryReceipt(Base):
    """Immutable proof that the bounded MP4+SRT archive recovery verified."""

    __tablename__ = "v2_drive_archive_property_limit_recovery_receipts"

    id: Mapped[uuid.UUID] = uuid_pk()
    authority_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "v2_drive_archive_property_limit_recovery_authorities.id",
            name="fk_v2_drive_archive_recovery_receipt_authority",
        ),
        nullable=False,
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "production_workflow_runs.id",
            name="fk_v2_drive_archive_recovery_receipt_workflow",
        ),
        nullable=False,
    )
    archive_effect_ledger_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "v2_production_effect_ledger.id",
            name="fk_v2_drive_archive_recovery_receipt_effect",
        ),
        nullable=False,
    )
    media_cloud_media_ref_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "cloud_media_refs.id",
            name="fk_v2_drive_archive_recovery_receipt_media_cloud",
        ),
        nullable=False,
    )
    caption_cloud_media_ref_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "cloud_media_refs.id",
            name="fk_v2_drive_archive_recovery_receipt_caption_cloud",
        ),
        nullable=False,
    )
    final_media_ref_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "final_media_refs.id",
            name="fk_v2_drive_archive_recovery_receipt_final_media",
        ),
        nullable=False,
    )
    media_drive_file_id: Mapped[str] = mapped_column(Text, nullable=False)
    caption_drive_file_id: Mapped[str] = mapped_column(Text, nullable=False)
    media_checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    caption_checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    archive_receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    archive_object_ref: Mapped[str] = mapped_column(Text, nullable=False)
    caption_archive_object_ref: Mapped[str] = mapped_column(Text, nullable=False)
    recovery_request_journal_ref: Mapped[str] = mapped_column(Text, nullable=False)
    recovery_request_journal_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    recovery_response_journal_ref: Mapped[str] = mapped_column(Text, nullable=False)
    recovery_response_journal_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    absence_reconciliation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actual_upload_submissions: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_verified_file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    automatic_publish: Mapped[bool] = mapped_column(Boolean, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    recovery_state: Mapped[str] = mapped_column(String(40), nullable=False)
    receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        CheckConstraint(
            "schema_version = 'vcos.v2-drive-archive-property-limit-recovery-receipt.v1' "
            "and recovery_state = 'VERIFIED' and actual_upload_submissions = 1 "
            "and provider_file_count = 2 and checksum_verified_file_count = 2 "
            "and not automatic_publish",
            name="ck_v2_drive_archive_recovery_receipt_identity",
        ),
        CheckConstraint(
            "media_checksum_sha256 ~ '^[0-9a-f]{64}$' and "
            "caption_checksum_sha256 ~ '^[0-9a-f]{64}$' and "
            "archive_receipt_hash ~ '^[0-9a-f]{64}$' and "
            "recovery_request_journal_hash ~ '^[0-9a-f]{64}$' and "
            "recovery_response_journal_hash ~ '^[0-9a-f]{64}$' and "
            "absence_reconciliation_hash ~ '^[0-9a-f]{64}$' and "
            "receipt_hash ~ '^[0-9a-f]{64}$'",
            name="ck_v2_drive_archive_recovery_receipt_hashes",
        ),
        Index("ix_v2_drive_archive_recovery_receipt_created_at", "created_at"),
        UniqueConstraint(
            "authority_id", name="uq_v2_drive_archive_recovery_receipt_authority"
        ),
        UniqueConstraint(
            "workflow_run_id", name="uq_v2_drive_archive_recovery_receipt_workflow"
        ),
        UniqueConstraint(
            "archive_effect_ledger_id",
            name="uq_v2_drive_archive_recovery_receipt_effect",
        ),
        UniqueConstraint(
            "media_cloud_media_ref_id",
            name="uq_v2_drive_archive_recovery_receipt_media_cloud",
        ),
        UniqueConstraint(
            "caption_cloud_media_ref_id",
            name="uq_v2_drive_archive_recovery_receipt_caption_cloud",
        ),
        UniqueConstraint(
            "final_media_ref_id",
            name="uq_v2_drive_archive_recovery_receipt_final_media",
        ),
        UniqueConstraint(
            "receipt_hash", name="uq_v2_drive_archive_recovery_receipt_hash"
        ),
    )


@event.listens_for(V2ProductionEffectLedger, "before_update")
def _verified_v2_effect_update_forbidden(
    _mapper: Mapper[V2ProductionEffectLedger],
    _connection: Any,
    target: V2ProductionEffectLedger,
) -> None:
    history = inspect(target).attrs.state.history
    previous = history.deleted[0] if history.deleted else target.state
    if previous == "VERIFIED":
        raise RuntimeError("V2_PRODUCTION_EFFECT_VERIFIED_IMMUTABLE")


@event.listens_for(V2ProductionEffectLedger, "before_delete")
def _verified_v2_effect_delete_forbidden(
    _mapper: Mapper[V2ProductionEffectLedger],
    _connection: Any,
    target: V2ProductionEffectLedger,
) -> None:
    if target.state == "VERIFIED":
        raise RuntimeError("V2_PRODUCTION_EFFECT_VERIFIED_IMMUTABLE")


def _immutable_v2_timing_recovery_update(
    _mapper: Mapper[Any], _connection: Any, _target: Any
) -> None:
    raise RuntimeError("V2_NARRATION_TIMING_RECOVERY_IMMUTABLE")


def _immutable_v2_timing_recovery_delete(
    _mapper: Mapper[Any], _connection: Any, _target: Any
) -> None:
    raise RuntimeError("V2_NARRATION_TIMING_RECOVERY_IMMUTABLE")


for _model in (
    V2NarrationTimingRecoveryAuthority,
    V2NarrationTimingRecoveryReceipt,
    V2DriveArchivePropertyLimitRecoveryAuthority,
    V2DriveArchivePropertyLimitRecoveryReceipt,
):
    event.listen(_model, "before_update", _immutable_v2_timing_recovery_update)
    event.listen(_model, "before_delete", _immutable_v2_timing_recovery_delete)


__all__ = [
    "V2DriveArchivePropertyLimitRecoveryAuthority",
    "V2DriveArchivePropertyLimitRecoveryReceipt",
    "V2NarrationTimingRecoveryAuthority",
    "V2NarrationTimingRecoveryReceipt",
    "V2ProductionEffectLedger",
]
