import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


HookType = Literal["DIRECT", "CONTRAST", "RISK", "OUTCOME", "QUESTION", "OTHER"]
ClickbaitRisk = Literal["LOW", "MEDIUM", "HIGH"]
PackagingGateStatus = Literal["PASS", "REVIEW_REQUIRED", "BLOCK", "SKIPPED_NOT_APPLICABLE"]


class HookSpecRead(BaseModel):
    id: str
    package_id: uuid.UUID
    video_project_id: uuid.UUID | None = None
    effective_context_snapshot_id: uuid.UUID | None = None
    hook_type: HookType = "OTHER"
    first_3_seconds_script: str | None = None
    first_3_seconds_visual: str | None = None
    promise_made: str | None = None
    payoff_location: str | None = None
    clickbait_risk: ClickbaitRisk = "MEDIUM"
    visual_hook_relevance: str | None = None
    title_hook_alignment: str | None = None
    evidence_refs_json: list[dict[str, Any]] = Field(default_factory=list)
    contract_paths_used_json: list[str] = Field(default_factory=list)
    content_hash: str
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class UploadHandoffCopyRead(BaseModel):
    title: str | None = None
    description: str | None = None
    hashtags_json: list[str] | None = None
    subtitle_refs_json: list[dict[str, Any]] = Field(default_factory=list)
    disclosure_notes_json: list[dict[str, Any]] = Field(default_factory=list)
    checklist_items_json: list[dict[str, Any]] = Field(default_factory=list)
    language: str | None = None
    locale: str | None = None
    channel_contract_hash: str | None = None
    effective_context_snapshot_id: uuid.UUID | None = None
    packaging_gate_status: PackagingGateStatus = "REVIEW_REQUIRED"
    source_artifact_refs_json: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class ThumbnailHandoffRead(BaseModel):
    concept: str | None = None
    text_overlay: str | None = None
    main_subject: str | None = None
    composition: str | None = None
    mobile_readability_notes: str | None = None
    thumbnail_ref: Any | None = None
    drive_ref: Any | None = None
    character_image_branch_id: uuid.UUID | str | None = None
    reference_asset_pack_id: uuid.UUID | str | None = None
    thumbnail_variant_plan_json: list[dict[str, Any]] | dict[str, Any] | None = None
    contract_paths_used_json: list[str] = Field(default_factory=list)
    source_artifact_refs_json: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class PublishTimingRecommendationRead(BaseModel):
    channel_timezone: str | None = None
    audience_timezone: str | None = None
    operator_local_timezone: str | None = None
    configured_publish_window_json: Any | None = None
    suggested_publish_time_channel_tz: str | None = None
    suggested_publish_time_operator_local: str | None = None
    publish_timing_policy_ref: str | None = None
    manual_publish_only: bool = True
    source_contract_paths: list[str] = Field(default_factory=list)
    reason_codes_json: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class PackagingGateResultRead(BaseModel):
    gate_key: str
    status: PackagingGateStatus
    reason_codes: list[str] = Field(default_factory=list)
    checked_artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    checked_contract_paths: list[str] = Field(default_factory=list)
    summary_vi: str
    next_action_vi: str | None = None

    model_config = ConfigDict(extra="forbid")


class PackagingGateSummaryRead(BaseModel):
    overall_status: PackagingGateStatus
    gate_results: list[PackagingGateResultRead] = Field(default_factory=list)
    r3d4_gate_batch_refs: list[str] = Field(default_factory=list)
    next_action_vi: str

    model_config = ConfigDict(extra="forbid")


class PackagingHandoffSnapshotRead(BaseModel):
    package_id: uuid.UUID
    package_status: str
    channel_id: uuid.UUID
    video_project_id: uuid.UUID | None = None
    effective_context_snapshot_id: uuid.UUID | None = None
    effective_context_hash: str | None = None
    hook_spec: HookSpecRead
    upload_handoff_copy: UploadHandoffCopyRead
    thumbnail_handoff: ThumbnailHandoffRead
    publish_timing_recommendation: PublishTimingRecommendationRead
    packaging_gate_summary: PackagingGateSummaryRead
    manual_upload: dict[str, Any] = Field(default_factory=dict)
    provider_readiness_summary: dict[str, Any] = Field(default_factory=dict)
    manual_publish_only: bool = True
    no_upload_or_publish_calls_made: bool = True
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")
