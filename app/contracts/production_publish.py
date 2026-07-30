"""Contracts for the Phase 5 final-review and canonical manual-publish lane."""

from __future__ import annotations

import uuid
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


SHA256_PATTERN = r"^[0-9a-f]{64}$"

PUBLISH_MATERIALITY_POLICY_V1: dict[str, Any] = {
    "policy_version": "vcos.publish-materiality.v1",
    "fields": {
        "platform": "CRITICAL",
        "platform_channel_id": "CRITICAL",
        "destination_account_identity": "CRITICAL",
        "platform_video_identity": "CRITICAL",
        "privacy_status": "CRITICAL",
        "disclosures": "CRITICAL",
        "title": "MATERIAL",
        "description": "NON_MATERIAL_WITH_ATTESTATION",
    },
}


class FinalVideoDecisionValue(StrEnum):
    UPLOAD = "UPLOAD"
    DO_NOT_UPLOAD = "DO_NOT_UPLOAD"


class HumanUploadTaskStateV2(StrEnum):
    READY_FOR_OPERATOR = "READY_FOR_OPERATOR"
    IN_PROGRESS = "IN_PROGRESS"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    VERIFIED = "VERIFIED"
    CANCELED = "CANCELED"


class ManualPublishConfirmationStateV2(StrEnum):
    SUBMITTED = "SUBMITTED"
    VERIFIED = "VERIFIED"
    REJECTED_MISMATCH = "REJECTED_MISMATCH"
    BLOCKED_DESTINATION = "BLOCKED_DESTINATION"
    CORRECTION_REQUIRED = "CORRECTION_REQUIRED"
    VARIANCE_ACCEPTED = "VARIANCE_ACCEPTED"
    CANCELED = "CANCELED"


class FinalReviewCandidateCreateV2(BaseModel):
    """Trusted coordinator input; this contract is intentionally not public."""

    workflow_run_id: uuid.UUID
    production_package_artifact_version_id: uuid.UUID
    production_package_hash: str = Field(pattern=SHA256_PATTERN)
    production_readiness_receipt_artifact_version_id: uuid.UUID
    production_readiness_receipt_hash: str = Field(pattern=SHA256_PATTERN)
    canonical_media_timeline_ref: str = Field(min_length=1)
    canonical_media_timeline_hash: str = Field(pattern=SHA256_PATTERN)
    native_render_plan_ref: str = Field(min_length=1)
    native_render_plan_hash: str = Field(pattern=SHA256_PATTERN)
    render_output_ref: str = Field(min_length=1)
    render_output_checksum: str = Field(pattern=SHA256_PATTERN)
    technical_qc_receipt_ref: str = Field(min_length=1)
    technical_qc_receipt_hash: str = Field(pattern=SHA256_PATTERN)
    technical_qc_state: Literal["PASS"]
    creative_qc_receipt_ref: str = Field(min_length=1)
    creative_qc_receipt_hash: str = Field(pattern=SHA256_PATTERN)
    creative_qc_state: Literal["PASS"]
    archive_receipt_ref: str = Field(min_length=1)
    archive_receipt_hash: str = Field(pattern=SHA256_PATTERN)
    archive_object_ref: str = Field(min_length=1)
    archive_verification_state: Literal["VERIFIED"]
    final_media_ref_id: uuid.UUID
    destination_binding_id: uuid.UUID
    destination_binding_fingerprint: str = Field(pattern=SHA256_PATTERN)
    destination_platform_channel_id: str = Field(min_length=1)
    destination_account_identity: str = Field(min_length=1)
    target_platform: str = Field(min_length=1, max_length=40)
    target_surface: str = Field(min_length=1, max_length=40)
    target_market_lineage: dict[str, Any] = Field(min_length=1)
    publish_metadata_snapshot: dict[str, Any] = Field(min_length=1)
    disclosure_snapshot: dict[str, Any] = Field(default_factory=dict)
    materiality_policy_snapshot: dict[str, Any] = Field(
        default_factory=lambda: {
            "policy_version": PUBLISH_MATERIALITY_POLICY_V1["policy_version"],
            "fields": dict(PUBLISH_MATERIALITY_POLICY_V1["fields"]),
        }
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("target_platform")
    @classmethod
    def normalize_platform(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("TARGET_PLATFORM_REQUIRED")
        return normalized

    @model_validator(mode="after")
    def validate_materiality_authority(self) -> "FinalReviewCandidateCreateV2":
        if self.materiality_policy_snapshot != PUBLISH_MATERIALITY_POLICY_V1:
            raise ValueError("UNSUPPORTED_PUBLISH_MATERIALITY_POLICY")
        required_metadata = {"title", "privacy_status"}
        if not required_metadata.issubset(self.publish_metadata_snapshot):
            raise ValueError("PUBLISH_METADATA_SNAPSHOT_INCOMPLETE")
        return self


class FinalReviewCandidateRead(BaseModel):
    id: uuid.UUID
    workflow_run_id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    channel_profile_version_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    production_package_artifact_version_id: uuid.UUID
    production_package_hash: str
    production_readiness_receipt_artifact_version_id: uuid.UUID
    production_readiness_receipt_hash: str
    canonical_media_timeline_ref: str
    canonical_media_timeline_hash: str
    native_render_plan_ref: str
    native_render_plan_hash: str
    render_output_ref: str
    render_output_checksum: str
    technical_qc_receipt_ref: str
    technical_qc_receipt_hash: str
    creative_qc_receipt_ref: str
    creative_qc_receipt_hash: str
    archive_receipt_ref: str
    archive_receipt_hash: str
    archive_object_ref: str
    archive_verification_state: Literal["VERIFIED"]
    final_media_ref_id: uuid.UUID
    final_media_hash: str
    destination_binding_id: uuid.UUID
    destination_binding_fingerprint: str
    destination_platform_channel_id: str
    destination_account_identity: str
    target_platform: str
    target_surface: str
    target_market_lineage: dict[str, Any]
    production_lane: str
    content_mode: str
    series_plan_id: uuid.UUID | None
    series_run_id: uuid.UUID | None
    episode_number: int | None
    standalone_reason_code: str | None
    parent_video_project_id: uuid.UUID | None
    parent_final_media_ref_id: uuid.UUID | None
    publish_metadata_snapshot: dict[str, Any]
    disclosure_snapshot: dict[str, Any]
    materiality_policy_snapshot: dict[str, Any]
    materiality_policy_hash: str
    candidate_hash: str
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class FinalVideoDecisionCreate(BaseModel):
    command_id: uuid.UUID
    decision: FinalVideoDecisionValue
    reason: str | None = Field(default=None, max_length=4000)
    warnings_acknowledged: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class FinalVideoDecisionRead(BaseModel):
    id: uuid.UUID
    final_review_candidate_id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    decision: FinalVideoDecisionValue
    operator_user_id: uuid.UUID
    authenticated_actor_role: str
    final_media_ref_id: uuid.UUID
    final_media_hash: str
    production_package_artifact_version_id: uuid.UUID
    production_package_hash: str
    destination_binding_id: uuid.UUID
    destination_binding_fingerprint: str
    command_id: uuid.UUID
    decision_timestamp: AwareDatetime
    reason: str | None
    warnings_acknowledged: list[str]
    decision_hash: str
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class FinalVideoDecisionResult(BaseModel):
    decision: FinalVideoDecisionRead
    human_upload_task_id: uuid.UUID | None

    model_config = ConfigDict(extra="forbid")


class HumanUploadTaskStartV2(BaseModel):
    selected_file_name: str = Field(min_length=1)
    selected_file_ref: str = Field(min_length=1)
    selected_file_checksum: str = Field(pattern=SHA256_PATTERN)
    archive_object_ref: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class HumanUploadTaskCancelV2(BaseModel):
    command_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=4000)

    model_config = ConfigDict(extra="forbid")


class HumanUploadTaskReadV2(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    destination: str
    target_platform: str
    task_state: HumanUploadTaskStateV2
    schema_version: Literal["v2"]
    final_review_candidate_id: uuid.UUID
    final_video_decision_id: uuid.UUID
    final_media_ref_id: uuid.UUID
    final_media_file_ref: str
    reviewed_checksum: str
    production_package_artifact_version_id: uuid.UUID
    production_package_hash: str
    destination_binding_id: uuid.UUID
    destination_binding_fingerprint: str
    channel_profile_version_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    production_lane: str
    content_mode: str
    series_plan_id: uuid.UUID | None
    series_run_id: uuid.UUID | None
    episode_number: int | None
    standalone_reason_code: str | None
    parent_video_project_id: uuid.UUID | None
    parent_final_media_ref_id: uuid.UUID | None
    archive_object_ref: str
    selected_file_name: str | None
    selected_file_ref: str | None
    selected_file_checksum: str | None
    attested_by_user_id: uuid.UUID | None
    attested_at: AwareDatetime | None
    started_by_user_id: uuid.UUID | None
    started_at: AwareDatetime | None
    cancel_command_id: uuid.UUID | None
    canceled_by_user_id: uuid.UUID | None
    canceled_at: AwareDatetime | None
    actual_uploaded_video_id: uuid.UUID | None
    completed_at: AwareDatetime | None
    blocked_reason: str | None
    operator_note: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ManualPublishConfirmationCreateV2(BaseModel):
    command_id: uuid.UUID
    platform: str = Field(min_length=1, max_length=40)
    platform_channel_id: str = Field(min_length=1)
    destination_binding_id: uuid.UUID
    destination_binding_fingerprint: str = Field(pattern=SHA256_PATTERN)
    destination_account_identity: str = Field(min_length=1)
    platform_video_id: str = Field(min_length=1)
    video_url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str | None = None
    privacy_status: str = Field(min_length=1, max_length=40)
    published_at: AwareDatetime
    duration_seconds: Decimal = Field(gt=0)
    thumbnail_confirmed: bool
    caption_confirmed: bool
    playlist_id: str | None = None
    playlist_order: int | None = Field(default=None, ge=0)
    disclosures: dict[str, Any] = Field(default_factory=dict)
    accept_non_material_variance: bool = False
    operator_notes: str | None = Field(default=None, max_length=4000)

    model_config = ConfigDict(extra="forbid")

    @field_validator("platform", "privacy_status")
    @classmethod
    def normalize_upper(cls, value: str) -> str:
        return value.strip().upper()


class ManualPublishCorrectionV2(ManualPublishConfirmationCreateV2):
    correction_command_id: uuid.UUID

    # Submission identity cannot be changed by a correction.
    command_id: uuid.UUID | None = Field(default=None, exclude=True)


class ManualPublishVerificationV2(BaseModel):
    verification_command_id: uuid.UUID
    verification_evidence_ref: str = Field(min_length=1)
    observed_platform: str = Field(min_length=1, max_length=40)
    observed_platform_channel_id: str = Field(min_length=1)
    observed_destination_account_identity: str = Field(min_length=1)
    observed_platform_video_id: str = Field(min_length=1)
    observed_video_url: str = Field(min_length=1)
    observed_title: str = Field(min_length=1)
    observed_description: str | None = None
    observed_privacy_status: str = Field(min_length=1, max_length=40)
    observed_published_at: AwareDatetime
    observed_duration_seconds: Decimal = Field(gt=0)

    model_config = ConfigDict(extra="forbid")

    @field_validator("observed_platform", "observed_privacy_status")
    @classmethod
    def normalize_upper(cls, value: str) -> str:
        return value.strip().upper()


class ManualPublishConfirmationReadV2(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    target_platform: str
    target_surface: str
    confirmed_by_user_id: uuid.UUID
    confirmation_state: ManualPublishConfirmationStateV2
    actual_video_id: str
    actual_video_url: str
    actual_published_at: AwareDatetime
    destination_binding_id: uuid.UUID
    destination_binding_fingerprint: str
    actual_metadata: dict[str, Any]
    actual_disclosures: dict[str, Any]
    operator_notes: str | None
    validation_summary: dict[str, Any]
    metadata_diff: dict[str, Any]
    reason_codes: list[str]
    next_action: str | None
    schema_version: Literal["v2"]
    command_id: uuid.UUID
    confirmation_hash: str
    human_upload_task_id: uuid.UUID
    final_review_candidate_id: uuid.UUID
    final_video_decision_id: uuid.UUID
    final_media_ref_id: uuid.UUID
    reviewed_checksum: str
    production_package_artifact_version_id: uuid.UUID
    production_package_hash: str
    policy_snapshot_id: uuid.UUID
    channel_profile_version_id: uuid.UUID
    platform_channel_id: str
    destination_account_identity: str
    actual_duration_seconds: Decimal
    thumbnail_confirmed: bool
    caption_confirmed: bool
    playlist_id: str | None
    playlist_order: int | None
    materiality_policy_hash: str
    variance_attested_by_user_id: uuid.UUID | None
    variance_attested_at: AwareDatetime | None
    corrected_by_user_id: uuid.UUID | None
    corrected_at: AwareDatetime | None
    correction_history: list[dict[str, Any]]
    verified_by_user_id: uuid.UUID | None
    verified_at: AwareDatetime | None
    verification_command_id: uuid.UUID | None
    verification_evidence_ref: str | None
    verification_evidence_hash: str | None
    canceled_by_user_id: uuid.UUID | None
    canceled_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class UploadedVideoReadV2(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    manual_publish_confirmation_id: uuid.UUID
    human_upload_task_id: uuid.UUID
    destination: str
    destination_binding_id: uuid.UUID
    destination_binding_fingerprint: str
    platform: str
    platform_video_id: str
    video_url: str
    published_at: AwareDatetime
    publish_status: str
    actual_metadata: dict[str, Any]
    actual_disclosures: dict[str, Any]
    lineage_refs: dict[str, Any]
    verification_status: Literal["VERIFIED"]
    analytics_sync_status: Literal["READY"]
    schema_version: Literal["v2"]
    final_review_candidate_id: uuid.UUID
    final_video_decision_id: uuid.UUID
    final_media_ref_id: uuid.UUID
    production_package_artifact_version_id: uuid.UUID
    production_package_hash: str
    channel_profile_version_id: uuid.UUID
    reviewed_checksum: str
    production_lane: str
    content_mode: str
    series_plan_id: uuid.UUID | None
    series_run_id: uuid.UUID | None
    episode_number: int | None
    standalone_reason_code: str | None
    parent_video_project_id: uuid.UUID | None
    parent_final_media_ref_id: uuid.UUID | None
    target_market_lineage: dict[str, Any]
    archive_supplement: dict[str, Any]
    archive_supplement_ref: str
    archive_supplement_hash: str
    verified_event_id: uuid.UUID
    analytics_ready_event_id: uuid.UUID
    analytics_ready_at: AwareDatetime
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ManualPublishVerificationResultV2(BaseModel):
    status: Literal[
        "VERIFIED",
        "REJECTED_MISMATCH",
        "BLOCKED_DESTINATION",
        "CORRECTION_REQUIRED",
    ]
    confirmation: ManualPublishConfirmationReadV2
    uploaded_video: UploadedVideoReadV2 | None = None

    model_config = ConfigDict(extra="forbid")
