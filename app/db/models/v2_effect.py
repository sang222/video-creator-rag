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
    recovered_timing_seed_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
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
        Index(
            "ix_v2_narration_timing_recovery_receipt_created_at", "created_at"
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
):
    event.listen(_model, "before_update", _immutable_v2_timing_recovery_update)
    event.listen(_model, "before_delete", _immutable_v2_timing_recovery_delete)


__all__ = [
    "V2NarrationTimingRecoveryAuthority",
    "V2NarrationTimingRecoveryReceipt",
    "V2ProductionEffectLedger",
]
