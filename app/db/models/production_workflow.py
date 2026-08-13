"""Thin durable production-workflow projections.

The rows in this module are deliberately not business authorities.  Admission,
package, readiness, media, QC, archive, and final-review truth remains in the
existing immutable domain rows.  ``ProductionWorkflowRun`` only projects those
exact authorities for sequencing and operator visibility.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
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

from app.core.time import utc_now
from app.db.base import Base
from app.db.models.foundation import (
    utc_created_at,
    utc_updated_at,
    uuid_pk,
)


WORKFLOW_STATES = (
    "PLANNING_PENDING",
    "PLANNING_RUNNING",
    "ASSIGNMENT_READY",
    "RESEARCH_PENDING",
    "RESEARCH_RUNNING",
    "PACKAGE_PENDING",
    "PACKAGE_RUNNING",
    "READY_FOR_PRODUCTION",
    "MEDIA_PENDING",
    "MEDIA_RUNNING",
    "VISUAL_PENDING",
    "VISUAL_RUNNING",
    "RENDER_PENDING",
    "RENDER_RUNNING",
    "QC_PENDING",
    "QC_RUNNING",
    "ARCHIVE_PENDING",
    "ARCHIVE_RUNNING",
    "PAUSED_AFTER_NATIVE_RENDER",
    "FINAL_REVIEW_READY",
    "BLOCKED",
    "RETRY_SCHEDULED",
    "CANCELED",
    "FAILED_TERMINAL",
    "DEAD_LETTERED",
    "SUPERSEDED",
)

WORKFLOW_STAGES = (
    "PLANNING",
    "PREFLIGHT",
    "ADMISSION",
    "RESEARCH",
    "PACKAGE",
    "READINESS",
    "MEDIA",
    "VISUAL",
    "RENDER",
    "QC",
    "ARCHIVE",
    "FINALIZE",
)


class ProductionWorkflowRun(Base):
    """Resumable operator projection over exact immutable domain authorities."""

    __tablename__ = "production_workflow_runs"

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
    uploaded_video_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("uploaded_videos.id")
    )
    production_lane: Mapped[str] = mapped_column(String(40), nullable=False)
    planning_source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    planning_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    planning_source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    start_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(40), nullable=False, default="PLANNING_PENDING"
    )
    current_stage: Mapped[str] = mapped_column(
        String(40), nullable=False, default="PLANNING"
    )
    state_reason_codes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    project_admission_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_admission_decisions.id")
    )
    project_admission_decision_hash: Mapped[str | None] = mapped_column(String(64))
    production_package_artifact_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_versions.id")
    )
    production_package_hash: Mapped[str | None] = mapped_column(String(64))
    production_readiness_receipt_artifact_version_id: Mapped[uuid.UUID | None] = (
        mapped_column(UUID(as_uuid=True), ForeignKey("artifact_versions.id"))
    )
    production_readiness_receipt_hash: Mapped[str | None] = mapped_column(String(64))
    canonical_media_timeline_ref: Mapped[str | None] = mapped_column(Text)
    canonical_media_timeline_hash: Mapped[str | None] = mapped_column(String(64))
    ai_visual_production_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "ai_visual_production_runs.id",
            name="fk_production_workflow_runs_ai_visual_production_run_id",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    ai_visual_policy_ref: Mapped[str | None] = mapped_column(Text)
    ai_visual_policy_hash: Mapped[str | None] = mapped_column(String(64))
    ai_visual_style_bible_ref: Mapped[str | None] = mapped_column(Text)
    ai_visual_style_bible_hash: Mapped[str | None] = mapped_column(String(64))
    ai_visual_scene_plan_ref: Mapped[str | None] = mapped_column(Text)
    ai_visual_scene_plan_hash: Mapped[str | None] = mapped_column(String(64))
    ai_visual_asset_manifest_ref: Mapped[str | None] = mapped_column(Text)
    ai_visual_asset_manifest_hash: Mapped[str | None] = mapped_column(String(64))
    video_motion_grammar_ref: Mapped[str | None] = mapped_column(Text)
    video_motion_grammar_hash: Mapped[str | None] = mapped_column(String(64))
    ffmpeg_effect_plan_ref: Mapped[str | None] = mapped_column(Text)
    ffmpeg_effect_plan_hash: Mapped[str | None] = mapped_column(String(64))
    native_render_plan_ref: Mapped[str | None] = mapped_column(Text)
    native_render_plan_hash: Mapped[str | None] = mapped_column(String(64))
    render_output_ref: Mapped[str | None] = mapped_column(Text)
    render_output_checksum: Mapped[str | None] = mapped_column(String(64))
    technical_qc_receipt_ref: Mapped[str | None] = mapped_column(Text)
    technical_qc_receipt_hash: Mapped[str | None] = mapped_column(String(64))
    creative_qc_receipt_ref: Mapped[str | None] = mapped_column(Text)
    creative_qc_receipt_hash: Mapped[str | None] = mapped_column(String(64))
    cross_modal_qc_receipt_ref: Mapped[str | None] = mapped_column(Text)
    cross_modal_qc_receipt_hash: Mapped[str | None] = mapped_column(String(64))
    archive_receipt_ref: Mapped[str | None] = mapped_column(Text)
    archive_receipt_hash: Mapped[str | None] = mapped_column(String(64))
    archive_object_ref: Mapped[str | None] = mapped_column(Text)
    archive_verification_state: Mapped[str | None] = mapped_column(String(40))
    final_media_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_media_refs.id")
    )
    final_media_ref_hash: Mapped[str | None] = mapped_column(String(64))
    final_review_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "final_review_candidates.id",
            name="fk_production_workflow_runs_final_review_candidate_id",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    final_review_candidate_artifact_version_id: Mapped[uuid.UUID | None] = (
        mapped_column(UUID(as_uuid=True), ForeignKey("artifact_versions.id"))
    )
    final_review_candidate_hash: Mapped[str | None] = mapped_column(String(64))
    destination_binding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    destination_binding_fingerprint: Mapped[str | None] = mapped_column(String(64))
    destination_binding: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    cancellation_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    cancellation_requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_progress_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        CheckConstraint(
            "production_lane = 'LONG_FORM'",
            name="production_workflow_runs_lane",
        ),
        CheckConstraint(
            "planning_source_type = 'LONG_FORM_PLAN'",
            name="production_workflow_runs_planning_source",
        ),
        CheckConstraint(
            "state in (" + ",".join(f"'{state}'" for state in WORKFLOW_STATES) + ")",
            name="production_workflow_runs_state",
        ),
        CheckConstraint(
            "current_stage in ("
            + ",".join(f"'{stage}'" for stage in WORKFLOW_STAGES)
            + ")",
            name="production_workflow_runs_stage",
        ),
        CheckConstraint(
            "planning_source_hash ~ '^[0-9a-f]{64}$' "
            "and workflow_key ~ '^[0-9a-f]{64}$' "
            "and start_input_hash ~ '^[0-9a-f]{64}$'",
            name="production_workflow_runs_identity_hashes",
        ),
        CheckConstraint(
            "projection_version > 0",
            name="production_workflow_runs_projection_version",
        ),
        CheckConstraint(
            "(state <> 'CANCELED') or "
            "(cancellation_requested_at is not null and canceled_at is not null)",
            name="production_workflow_runs_canceled_evidence",
        ),
        CheckConstraint(
            "(archive_verification_state is null) or "
            "(archive_verification_state in "
            "('NOT_STARTED','PENDING','VERIFIED','FAILED','UNCERTAIN'))",
            name="production_workflow_runs_archive_state",
        ),
        Index("ix_production_workflow_runs_company_id", "company_id"),
        Index(
            "ix_production_workflow_runs_channel_workspace_id",
            "channel_workspace_id",
        ),
        Index("ix_production_workflow_runs_video_project_id", "video_project_id"),
        Index("ix_production_workflow_runs_production_lane", "production_lane"),
        Index(
            "ix_production_workflow_runs_state_progress",
            "state",
            "last_progress_at",
        ),
        Index(
            "ix_production_workflow_runs_source",
            "planning_source_type",
            "planning_source_id",
        ),
        Index(
            "ix_production_workflow_runs_final_review_candidate_id",
            "final_review_candidate_id",
        ),
        Index("ix_production_workflow_runs_created_at", "created_at"),
    )


class WorkflowCommandReceipt(Base):
    """Immutable evidence that one deterministic workflow command completed."""

    __tablename__ = "workflow_command_receipts"

    id: Mapped[uuid.UUID] = uuid_pk()
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_workflow_runs.id"),
        nullable=False,
    )

    domain_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("domain_events.id"),
        nullable=False,
        unique=True,
    )
    command_id: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    handler_key: Mapped[str] = mapped_column(String(160), nullable=False)
    handler_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    effect_state: Mapped[str] = mapped_column(
        String(40), nullable=False, default="COMPLETED"
    )
    result_type: Mapped[str] = mapped_column(String(120), nullable=False)
    result_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    result_ref: Mapped[str | None] = mapped_column(Text)
    result_hash: Mapped[str | None] = mapped_column(String(64))
    result_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    authority_refs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        CheckConstraint(
            "stage in (" + ",".join(f"'{stage}'" for stage in WORKFLOW_STAGES) + ")",
            name="workflow_command_receipts_stage",
        ),
        CheckConstraint(
            "effect_state in ('COMPLETED','RECONCILED','CANCELED')",
            name="workflow_command_receipts_effect_state",
        ),
        CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'",
            name="workflow_command_receipts_input_hash",
        ),
        CheckConstraint(
            "result_hash is null or result_hash ~ '^[0-9a-f]{64}$'",
            name="workflow_command_receipts_result_hash",
        ),
        CheckConstraint(
            "completed_at >= started_at",
            name="workflow_command_receipts_completion_order",
        ),
        Index(
            "ix_workflow_command_receipts_workflow_stage",
            "workflow_run_id",
            "stage",
        ),
        Index(
            "ix_workflow_command_receipts_handler",
            "handler_key",
            "handler_version",
        ),
        Index("ix_workflow_command_receipts_created_at", "created_at"),
    )


class WorkflowHold(Base):
    """Workflow-scoped durable stop before archive/final-review effects."""

    __tablename__ = "workflow_holds"

    id: Mapped[uuid.UUID] = uuid_pk()
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_workflow_runs.id"),
        nullable=False,
    )
    requested_reason: Mapped[str] = mapped_column(String(160), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("workflow_run_id", name="uq_workflow_hold_run"),
        CheckConstraint(
            "state in ('PENDING','ACTIVE','RELEASED')",
            name="ck_workflow_hold_state",
        ),
        CheckConstraint(
            "(state = 'PENDING' and activated_at is null and released_at is null) or "
            "(state = 'ACTIVE' and activated_at is not null and released_at is null) or "
            "(state = 'RELEASED' and activated_at is not null and released_at is not null and release_reason is not null)",
            name="ck_workflow_hold_lifecycle",
        ),
        Index("ix_workflow_holds_state", "state", "updated_at"),
    )


class WorkflowRecoveryReceipt(Base):
    """Immutable proof for an automatic, zero-effect workflow supersession."""

    __tablename__ = "workflow_recovery_receipts"

    id: Mapped[uuid.UUID] = uuid_pk()
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_workflow_runs.id"),
        nullable=False,
        unique=True,
    )
    dead_letter_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dead_letter_jobs.id"),
        nullable=False,
        unique=True,
    )
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ops_incidents.id")
    )
    recovery_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domain_events.id"), nullable=False, unique=True
    )
    recovery_version: Mapped[str] = mapped_column(String(80), nullable=False)
    classification: Mapped[str] = mapped_column(String(80), nullable=False)
    decision: Mapped[str] = mapped_column(String(120), nullable=False)
    failed_stage: Mapped[str] = mapped_column(String(40), nullable=False)
    failure_reason_code: Mapped[str] = mapped_column(String(160), nullable=False)
    proof: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    decision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        CheckConstraint(
            "classification = 'STALE_PRE_REPAIR_ZERO_EFFECT_WORKFLOW'",
            name="workflow_recovery_receipts_classification",
        ),
        CheckConstraint(
            "decision = 'AUTO_SUPERSEDE_STALE_PRE_REPAIR_WORKFLOW'",
            name="workflow_recovery_receipts_decision",
        ),
        CheckConstraint(
            "failed_stage in ("
            + ",".join(f"'{stage}'" for stage in WORKFLOW_STAGES)
            + ")",
            name="workflow_recovery_receipts_stage",
        ),
        CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$' and decision_hash ~ '^[0-9a-f]{64}$'",
            name="workflow_recovery_receipts_hashes",
        ),
        Index("ix_workflow_recovery_receipts_created_at", "created_at"),
    )


@event.listens_for(WorkflowCommandReceipt, "before_update")
def _workflow_receipt_update_forbidden(
    _mapper: Mapper[WorkflowCommandReceipt],
    _connection: Any,
    _target: WorkflowCommandReceipt,
) -> None:
    raise RuntimeError("WORKFLOW_COMMAND_RECEIPT_IMMUTABLE")


@event.listens_for(WorkflowCommandReceipt, "before_delete")
def _workflow_receipt_delete_forbidden(
    _mapper: Mapper[WorkflowCommandReceipt],
    _connection: Any,
    _target: WorkflowCommandReceipt,
) -> None:
    raise RuntimeError("WORKFLOW_COMMAND_RECEIPT_IMMUTABLE")


@event.listens_for(WorkflowRecoveryReceipt, "before_update")
def _workflow_recovery_receipt_update_forbidden(
    _mapper: Mapper[WorkflowRecoveryReceipt],
    _connection: Any,
    _target: WorkflowRecoveryReceipt,
) -> None:
    raise RuntimeError("WORKFLOW_RECOVERY_RECEIPT_IMMUTABLE")


@event.listens_for(WorkflowRecoveryReceipt, "before_delete")
def _workflow_recovery_receipt_delete_forbidden(
    _mapper: Mapper[WorkflowRecoveryReceipt],
    _connection: Any,
    _target: WorkflowRecoveryReceipt,
) -> None:
    raise RuntimeError("WORKFLOW_RECOVERY_RECEIPT_IMMUTABLE")
