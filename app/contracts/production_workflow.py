"""Public and internal contracts for durable production orchestration."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.contracts.vcos_v2 import PlanningSourceType, ProductionLane


SHA256_PATTERN = r"^[0-9a-f]{64}$"


class ProductionWorkflowState(StrEnum):
    PLANNING_PENDING = "PLANNING_PENDING"
    PLANNING_RUNNING = "PLANNING_RUNNING"
    ASSIGNMENT_READY = "ASSIGNMENT_READY"
    RESEARCH_PENDING = "RESEARCH_PENDING"
    RESEARCH_RUNNING = "RESEARCH_RUNNING"
    PACKAGE_PENDING = "PACKAGE_PENDING"
    PACKAGE_RUNNING = "PACKAGE_RUNNING"
    READY_FOR_PRODUCTION = "READY_FOR_PRODUCTION"
    MEDIA_PENDING = "MEDIA_PENDING"
    MEDIA_RUNNING = "MEDIA_RUNNING"
    RENDER_PENDING = "RENDER_PENDING"
    RENDER_RUNNING = "RENDER_RUNNING"
    QC_PENDING = "QC_PENDING"
    QC_RUNNING = "QC_RUNNING"
    ARCHIVE_PENDING = "ARCHIVE_PENDING"
    ARCHIVE_RUNNING = "ARCHIVE_RUNNING"
    FINAL_REVIEW_READY = "FINAL_REVIEW_READY"
    BLOCKED = "BLOCKED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    CANCELED = "CANCELED"
    FAILED_TERMINAL = "FAILED_TERMINAL"
    DEAD_LETTERED = "DEAD_LETTERED"
    SUPERSEDED = "SUPERSEDED"


class ProductionWorkflowStage(StrEnum):
    PLANNING = "PLANNING"
    PREFLIGHT = "PREFLIGHT"
    ADMISSION = "ADMISSION"
    RESEARCH = "RESEARCH"
    PACKAGE = "PACKAGE"
    READINESS = "READINESS"
    MEDIA = "MEDIA"
    RENDER = "RENDER"
    QC = "QC"
    ARCHIVE = "ARCHIVE"
    FINALIZE = "FINALIZE"


class WorkflowFailureClassification(StrEnum):
    AUTO_RETRY_WITHIN_POLICY = "AUTO_RETRY_WITHIN_POLICY"
    POLICY_AUTHORIZED_LOCAL_REPAIR = "POLICY_AUTHORIZED_LOCAL_REPAIR"
    BLOCK_EXTERNAL_FAILURE = "BLOCK_EXTERNAL_FAILURE"
    FAIL_PERMANENT_POLICY = "FAIL_PERMANENT_POLICY"
    FAIL_PERMANENT_INTEGRITY = "FAIL_PERMANENT_INTEGRITY"


class WorkflowEffectState(StrEnum):
    COMPLETED = "COMPLETED"
    RECONCILED = "RECONCILED"
    CANCELED = "CANCELED"


class ProductionWorkflowStart(BaseModel):
    """One authenticated action that starts or returns a stable workflow."""

    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    production_lane: Literal[ProductionLane.LONG_FORM]
    planning_source_type: Literal[PlanningSourceType.LONG_FORM_PLAN]
    planning_source_id: uuid.UUID
    planning_source_hash: str = Field(pattern=SHA256_PATTERN)
    video_project_id: uuid.UUID | None = None
    max_attempts: int = Field(default=5, ge=1, le=20)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)

    model_config = ConfigDict(extra="forbid")


class ProductionWorkflowProjectStart(BaseModel):
    """Safe public start body; all authority identity is derived server-side."""

    max_attempts: int = Field(default=5, ge=1, le=5)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)

    model_config = ConfigDict(extra="forbid")


class ProductionWorkflowResume(BaseModel):
    reason_code: str = Field(default="OPERATOR_RESUME", min_length=1, max_length=160)

    model_config = ConfigDict(extra="forbid")


class ProductionWorkflowCancel(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)

    model_config = ConfigDict(extra="forbid")


class DeadLetterRetryRequest(BaseModel):
    reason_code: str = Field(
        default="OPERATOR_AUTHORIZED_RETRY", min_length=1, max_length=160
    )
    additional_attempts: int = Field(default=1, ge=1, le=5)

    model_config = ConfigDict(extra="forbid")


class WorkflowAuthorityRefs(BaseModel):
    """Exact receipt projection returned by a trusted stage handler."""

    video_project_id: uuid.UUID | None = None
    uploaded_video_id: uuid.UUID | None = None
    project_admission_decision_id: uuid.UUID | None = None
    project_admission_decision_hash: str | None = Field(
        default=None, pattern=SHA256_PATTERN
    )
    production_package_artifact_version_id: uuid.UUID | None = None
    production_package_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    production_readiness_receipt_artifact_version_id: uuid.UUID | None = None
    production_readiness_receipt_hash: str | None = Field(
        default=None, pattern=SHA256_PATTERN
    )
    canonical_media_timeline_ref: str | None = None
    canonical_media_timeline_hash: str | None = Field(
        default=None, pattern=SHA256_PATTERN
    )
    native_render_plan_ref: str | None = None
    native_render_plan_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    render_output_ref: str | None = None
    render_output_checksum: str | None = Field(default=None, pattern=SHA256_PATTERN)
    technical_qc_receipt_ref: str | None = None
    technical_qc_receipt_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    creative_qc_receipt_ref: str | None = None
    creative_qc_receipt_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    archive_receipt_ref: str | None = None
    archive_receipt_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    archive_object_ref: str | None = None
    archive_verification_state: str | None = None
    final_media_ref_id: uuid.UUID | None = None
    final_media_ref_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    final_review_candidate_id: uuid.UUID | None = None
    final_review_candidate_artifact_version_id: uuid.UUID | None = None
    final_review_candidate_hash: str | None = Field(
        default=None, pattern=SHA256_PATTERN
    )
    destination_binding_id: uuid.UUID | None = None
    destination_binding_fingerprint: str | None = Field(
        default=None, pattern=SHA256_PATTERN
    )
    destination_binding: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_exact_pairs(self) -> Self:
        pairs = (
            (
                self.project_admission_decision_id,
                self.project_admission_decision_hash,
                "project admission",
            ),
            (
                self.production_package_artifact_version_id,
                self.production_package_hash,
                "production package",
            ),
            (
                self.production_readiness_receipt_artifact_version_id,
                self.production_readiness_receipt_hash,
                "production readiness",
            ),
            (
                self.canonical_media_timeline_ref,
                self.canonical_media_timeline_hash,
                "canonical media timeline",
            ),
            (
                self.native_render_plan_ref,
                self.native_render_plan_hash,
                "native render plan",
            ),
            (
                self.render_output_ref,
                self.render_output_checksum,
                "render output",
            ),
            (
                self.technical_qc_receipt_ref,
                self.technical_qc_receipt_hash,
                "technical QC",
            ),
            (
                self.creative_qc_receipt_ref,
                self.creative_qc_receipt_hash,
                "creative QC",
            ),
            (
                self.archive_receipt_ref,
                self.archive_receipt_hash,
                "archive receipt",
            ),
            (
                self.final_media_ref_id,
                self.final_media_ref_hash,
                "final media",
            ),
            (
                self.final_review_candidate_id,
                self.final_review_candidate_hash,
                "final review candidate",
            ),
            (
                self.destination_binding_id,
                self.destination_binding_fingerprint,
                "destination binding",
            ),
        )
        for identity, digest, label in pairs:
            if (identity is None) != (digest is None):
                raise ValueError(f"{label} identity and hash must be supplied together")
        if (
            self.final_review_candidate_artifact_version_id is not None
            and self.final_review_candidate_hash is None
        ):
            raise ValueError("final review candidate artifact requires candidate hash")
        if (
            self.archive_verification_state is not None
            and self.archive_verification_state
            not in {"NOT_STARTED", "PENDING", "VERIFIED", "FAILED", "UNCERTAIN"}
        ):
            raise ValueError("invalid archive verification state")
        return self


class WorkflowStageResult(BaseModel):
    result_type: str = Field(min_length=1, max_length=120)
    result_id: uuid.UUID | None = None
    result_ref: str | None = None
    result_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    result_payload: dict[str, Any] = Field(default_factory=dict)
    authority_refs: WorkflowAuthorityRefs = Field(default_factory=WorkflowAuthorityRefs)
    reason_codes: list[str] = Field(default_factory=list)
    effect_state: WorkflowEffectState = WorkflowEffectState.COMPLETED

    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkflowStageEventPayload(BaseModel):
    workflow_run_id: uuid.UUID
    production_lane: Literal[ProductionLane.LONG_FORM]
    stage: ProductionWorkflowStage
    handler_key: str = Field(min_length=1, max_length=160)
    input_hash: str = Field(pattern=SHA256_PATTERN)

    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkflowCommandReceiptRead(BaseModel):
    id: uuid.UUID
    workflow_run_id: uuid.UUID
    domain_event_id: uuid.UUID
    command_id: str
    stage: ProductionWorkflowStage
    handler_key: str
    handler_version: str
    input_hash: str = Field(pattern=SHA256_PATTERN)
    effect_state: WorkflowEffectState
    result_type: str
    result_id: uuid.UUID | None
    result_ref: str | None
    result_hash: str | None
    result_payload: dict[str, Any]
    authority_refs: WorkflowAuthorityRefs
    started_at: AwareDatetime
    completed_at: AwareDatetime
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductionWorkflowRead(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID | None
    uploaded_video_id: uuid.UUID | None
    production_lane: Literal[ProductionLane.LONG_FORM]
    planning_source_type: Literal[PlanningSourceType.LONG_FORM_PLAN]
    planning_source_id: uuid.UUID
    planning_source_hash: str
    workflow_key: str
    start_input_hash: str
    state: ProductionWorkflowState
    current_stage: ProductionWorkflowStage
    state_reason_codes: list[str]
    projection_version: int
    authority_refs: WorkflowAuthorityRefs
    cancellation_requested_at: AwareDatetime | None
    cancellation_requested_by_user_id: uuid.UUID | None
    cancellation_reason: str | None
    canceled_at: AwareDatetime | None
    started_at: AwareDatetime
    completed_at: AwareDatetime | None
    last_progress_at: AwareDatetime
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductionWorkflowList(BaseModel):
    items: list[ProductionWorkflowRead]
    count: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid", frozen=True)


class DeadLetterRetryRead(BaseModel):
    dead_letter_job_id: uuid.UUID
    workflow_run_id: uuid.UUID
    domain_event_id: uuid.UUID
    command_id: str
    replay_state: str
    next_attempt_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", frozen=True)
