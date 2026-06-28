import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


PublishTargetPlatform = Literal["YOUTUBE", "YOUTUBE_SHORTS", "TIKTOK", "FACEBOOK", "INSTAGRAM", "GENERIC"]
PublishTargetSurface = Literal["LONG_FORM", "SHORT_FORM", "REELS", "FEED", "STORY", "GENERIC"]
PublishHandoffState = Literal["DRAFT", "READY_FOR_OPERATOR", "BLOCKED", "CONFIRMED_PUBLISHED", "CANCELLED"]
PublishChecklistState = Literal["PENDING", "CONFIRMED", "NOT_REQUIRED", "BLOCKED"]
PublishChecklistCategory = Literal[
    "FILE_READY",
    "METADATA_READY",
    "THUMBNAIL_READY",
    "CAPTIONS_READY",
    "AI_DISCLOSURE",
    "PAID_PROMOTION_DISCLOSURE",
    "MUSIC_LICENSE",
    "STOCK_LICENSE",
    "RIGHTS_ENVELOPE",
    "PRIVACY_STATUS",
    "PLATFORM_SURFACE",
    "FINAL_HUMAN_REVIEW",
]
PrivacyStatus = Literal["PUBLIC", "UNLISTED", "PRIVATE", "SCHEDULED", "UNKNOWN"]
ManualPublishConfirmationState = Literal["DRAFT", "SUBMITTED", "ACCEPTED", "REVIEW_REQUIRED", "REJECTED", "CANCELLED"]
UploadedVideoPublishStatus = Literal["CONFIRMED", "REVIEW_REQUIRED", "REMOVED", "UNKNOWN"]
UploadedVideoMonitoringState = Literal["NOT_STARTED", "READY_FOR_ANALYTICS", "PAUSED", "NOT_SUPPORTED"]
MetadataDiffSeverity = Literal["NONE", "LOW", "MEDIUM", "HIGH"]
OperatorStatus = Literal[
    "READY_FOR_ANALYTICS",
    "NEEDS_DISCLOSURE_REVIEW",
    "NEEDS_LICENSE_REVIEW",
    "NEEDS_METADATA_REVIEW",
    "CONFIRMED",
]
FreshnessState = Literal["NOT_STARTED", "CURRENT", "STALE", "UNKNOWN"]


class PublishChecklistItem(BaseModel):
    item_id: str = Field(min_length=1)
    category: PublishChecklistCategory
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    required: bool
    state: PublishChecklistState
    reason_code: str | None = None
    evidence_ref: str | None = None
    operator_help_text: str | None = None

    model_config = ConfigDict(extra="forbid")


class PublishChecklistContract(BaseModel):
    target_platform: PublishTargetPlatform
    target_surface: PublishTargetSurface
    items: list[PublishChecklistItem] = Field(min_length=1)
    blocking_reason_codes: list[str] = Field(default_factory=list)
    operator_summary: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class PlatformPublishInstructionContract(BaseModel):
    target_platform: PublishTargetPlatform
    target_surface: PublishTargetSurface
    upload_file_instruction: str
    title_instruction: str
    description_instruction: str
    thumbnail_instruction: str
    caption_instruction: str
    ai_disclosure_instruction: str
    paid_promotion_instruction: str
    pre_publish_verification: list[str] = Field(default_factory=list)
    copy_back_fields: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class PlannedPublishMetadataContract(BaseModel):
    planned_title: str = Field(min_length=1)
    planned_description: str | None = None
    planned_tags: list[str] = Field(default_factory=list)
    planned_hashtags: list[str] = Field(default_factory=list)
    planned_category: str | None = None
    planned_language: str | None = None
    planned_thumbnail_ref: dict[str, Any] | None = None
    planned_caption_ref: dict[str, Any] | None = None
    planned_privacy_status: PrivacyStatus = "UNKNOWN"
    planned_made_for_kids: bool | None = None
    planned_ai_disclosure_required: bool = False
    planned_ai_disclosure_reason: str | None = None
    planned_paid_promotion_disclosure_required: bool = False
    planned_music_license_summary: str | None = None
    planned_rights_summary: dict[str, Any] = Field(default_factory=dict)
    planned_source_summary: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class PublishMetadataDiffContract(BaseModel):
    title_changed: bool = False
    description_changed: bool = False
    tags_changed: bool = False
    thumbnail_changed: bool = False
    privacy_status_changed: bool = False
    disclosure_changed: bool = False
    changed_fields: list[str] = Field(default_factory=list)
    severity: MetadataDiffSeverity = "NONE"
    requires_review: bool = False
    operator_summary: str = "No planned-vs-actual metadata changes."

    model_config = ConfigDict(extra="forbid")


class ActualPublishMetadataContract(BaseModel):
    actual_title: str = Field(min_length=1)
    actual_description: str | None = None
    actual_tags: list[str] | None = None
    actual_hashtags: list[str] | None = None
    actual_category: str | None = None
    actual_language: str | None = None
    actual_privacy_status: PrivacyStatus
    actual_thumbnail_ref: dict[str, Any] | None = None
    actual_thumbnail_hash: str | None = None
    actual_caption_uploaded: bool | None = None
    actual_made_for_kids: bool | None = None

    model_config = ConfigDict(extra="forbid")


class ActualDisclosureConfirmationContract(BaseModel):
    ai_disclosure_confirmed: bool
    ai_disclosure_label_used: str | None = None
    paid_promotion_disclosure_confirmed: bool | None = None
    music_license_confirmed: bool | None = None
    stock_license_confirmed: bool | None = None
    rights_confirmed: bool
    operator_confirmed_no_unlicensed_assets: bool | None = None

    model_config = ConfigDict(extra="forbid")


class PublishHandoffCreate(BaseModel):
    render_package_snapshot_id: uuid.UUID
    target_platform: PublishTargetPlatform = "YOUTUBE"
    target_surface: PublishTargetSurface = "LONG_FORM"
    destination_binding_id: uuid.UUID | None = None
    render_variant_id: str | None = None
    created_by_user_id: uuid.UUID | None = None
    planned_metadata_overrides: dict[str, Any] = Field(default_factory=dict)
    cloud_media_refs: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class PublishHandoffRead(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    production_artifact_run_id: uuid.UUID | None
    render_package_snapshot_id: uuid.UUID
    render_spec_snapshot_id: uuid.UUID | None
    media_qc_report_id: uuid.UUID | None
    accessibility_qc_report_id: uuid.UUID | None
    source_manifest_snapshot_id: uuid.UUID | None
    asset_manifest_snapshot_id: uuid.UUID | None
    target_platform: PublishTargetPlatform
    target_surface: PublishTargetSurface
    destination_binding_id: uuid.UUID | None
    render_variant_id: str | None
    package_state: PublishHandoffState
    planned_metadata: dict[str, Any]
    planned_disclosures: dict[str, Any]
    planned_files: dict[str, Any]
    cloud_media_refs: list[dict[str, Any]]
    checklist_snapshot: dict[str, Any]
    operator_instructions: dict[str, Any]
    risk_summary: dict[str, Any]
    reason_codes: list[str]
    next_action: str | None
    created_by_user_id: uuid.UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class ManualPublishConfirmationCreate(BaseModel):
    publish_handoff_package_id: uuid.UUID
    confirmed_by_user_id: uuid.UUID | None = None
    actual_video_id: str | None = None
    actual_video_url: str | None = None
    actual_published_at: AwareDatetime | None = None
    actual_metadata: dict[str, Any] = Field(default_factory=dict)
    actual_disclosures: dict[str, Any] = Field(default_factory=dict)
    actual_files: dict[str, Any] = Field(default_factory=dict)
    operator_notes: str | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_presence(self) -> "ManualPublishConfirmationCreate":
        missing: list[str] = []
        if not self.actual_video_id:
            missing.append("actual_video_id")
        if not self.actual_video_url:
            missing.append("actual_video_url")
        if self.actual_published_at is None:
            missing.append("actual_published_at")
        if missing:
            raise ValueError(f"manual publish confirmation requires: {', '.join(missing)}")
        return self


class ManualPublishConfirmationRead(BaseModel):
    id: uuid.UUID
    publish_handoff_package_id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    target_platform: PublishTargetPlatform
    target_surface: PublishTargetSurface
    confirmed_by_user_id: uuid.UUID | None
    confirmation_state: ManualPublishConfirmationState
    actual_video_id: str | None
    actual_video_url: str | None
    actual_published_at: AwareDatetime | None
    actual_metadata: dict[str, Any]
    actual_disclosures: dict[str, Any]
    actual_files: dict[str, Any]
    operator_notes: str | None
    validation_summary: dict[str, Any]
    metadata_diff: dict[str, Any]
    reason_codes: list[str]
    next_action: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class UploadedVideoRead(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID | None
    policy_snapshot_id: uuid.UUID | None
    publish_handoff_package_id: uuid.UUID | None
    manual_publish_confirmation_id: uuid.UUID | None
    render_package_snapshot_id: uuid.UUID | None
    first_scripted_video_package_id: uuid.UUID | None = None
    human_upload_task_id: uuid.UUID | None = None
    destination: str = "YOUTUBE"
    source_manifest_snapshot_id: uuid.UUID | None
    rights_envelope_ref: str | None
    platform: PublishTargetPlatform
    platform_video_id: str
    video_url: str
    external_video_id: str | None = None
    external_url: str | None = None
    published_at: AwareDatetime
    publish_status: UploadedVideoPublishStatus
    actual_metadata: dict[str, Any]
    actual_disclosures: dict[str, Any]
    lineage_refs: dict[str, Any]
    monitoring_state: UploadedVideoMonitoringState
    operator_summary: dict[str, Any]
    actual_title: str | None = None
    actual_visibility: PrivacyStatus = "UNKNOWN"
    actual_publish_time: AwareDatetime | None = None
    actual_upload_time: AwareDatetime | None = None
    playlist_id: str | None = None
    thumbnail_uploaded: bool | None = None
    subtitles_uploaded: bool | None = None
    description_modified_from_package: bool | None = None
    package_metadata_diff: dict[str, Any] | None = None
    verification_status: str = "NOT_VERIFIED"
    analytics_sync_status: str = "NOT_STARTED"
    last_verified_at: AwareDatetime | None = None
    last_analytics_sync_at: AwareDatetime | None = None
    operator_note: str | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class UploadedVideoPublicationSummaryRead(BaseModel):
    id: uuid.UUID
    uploaded_video_id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    platform: PublishTargetPlatform
    platform_video_id: str
    video_url: str
    published_at: AwareDatetime
    title: str
    publish_status: UploadedVideoPublishStatus
    monitoring_state: UploadedVideoMonitoringState
    operator_status: OperatorStatus
    operator_summary: str
    next_action: str | None
    freshness_state: FreshnessState
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")
