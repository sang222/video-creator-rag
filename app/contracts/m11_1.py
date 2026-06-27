import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


OperatorUserRole = Literal[
    "OWNER_ADMIN",
    "CHANNEL_MANAGER",
    "PRODUCER",
    "REVIEWER",
    "PUBLISHER",
    "ANALYST",
    "PROCUREMENT_OPERATOR",
    "COMPLIANCE_REVIEWER",
    "LEARNING_REVIEWER",
    "READ_ONLY",
]
OperatorUserStatus = Literal["ACTIVE", "DISABLED"]
TranslationMode = Literal["HUMAN_REVIEW_REQUIRED", "MACHINE_DRAFT_ONLY", "DISABLED"]
LocalizedSubtitleTranslationStatus = Literal["DRAFT", "MACHINE_DRAFT", "NEEDS_HUMAN_REVIEW", "APPROVED", "REJECTED", "BLOCKED"]
LocalizedSubtitleReviewStatus = Literal["NOT_REQUIRED", "NEEDS_REVIEW", "APPROVED", "REJECTED", "BLOCKED"]
LocalizedMetadataReviewStatus = Literal["DRAFT", "NEEDS_HUMAN_REVIEW", "APPROVED", "REJECTED", "BLOCKED"]
LocalizationReadinessResult = Literal["PASS", "REVIEW_REQUIRED", "BLOCK", "NOT_REQUIRED"]
PublishTimingSource = Literal["CHANNEL_CONFIG", "HUMAN_OVERRIDE", "ANALYTICS_OBSERVED_LATER"]
PublishTimingConfidence = Literal["CONFIGURED", "OBSERVED", "UNKNOWN"]


class AuthLoginRequest(BaseModel):
    email: str
    password: str

    model_config = ConfigDict(extra="forbid")


class CurrentOperatorUserRead(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    role: OperatorUserRole
    status: OperatorUserStatus

    model_config = ConfigDict(extra="forbid")


class AuthSessionRead(BaseModel):
    authenticated: bool
    auth_enabled: bool
    auth_mode: str
    local_dev_note: str
    user: CurrentOperatorUserRead | None = None

    model_config = ConfigDict(extra="forbid")


class ChannelLocalizationConfig(BaseModel):
    channel_workspace_id: uuid.UUID
    primary_language: str
    primary_region: str | None = None
    primary_timezone: str
    target_subtitle_languages: list[str] = Field(default_factory=list)
    target_metadata_languages: list[str] = Field(default_factory=list)
    target_regions: list[str] = Field(default_factory=list)
    translation_mode: TranslationMode = "DISABLED"
    localization_required_for_publish: bool = False
    localized_metadata_required: bool = False
    operator_summary: str
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ChannelLocalizationConfigUpdate(BaseModel):
    primary_language: str
    primary_region: str | None = None
    primary_timezone: str
    target_subtitle_languages: list[str] = Field(default_factory=list)
    target_metadata_languages: list[str] = Field(default_factory=list)
    target_regions: list[str] = Field(default_factory=list)
    translation_mode: TranslationMode = "DISABLED"
    localization_required_for_publish: bool = False
    localized_metadata_required: bool = False
    actor_role: OperatorUserRole = "OWNER_ADMIN"
    edited_by_user_id: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


class LocalizedSubtitlePackageCreate(BaseModel):
    source_language: str
    target_language: str
    base_caption_track_id: uuid.UUID | None = None
    srt_cloud_media_ref_id: uuid.UUID | None = None
    vtt_cloud_media_ref_id: uuid.UUID | None = None
    translation_status: LocalizedSubtitleTranslationStatus = "NEEDS_HUMAN_REVIEW"
    human_review_status: LocalizedSubtitleReviewStatus = "NEEDS_REVIEW"
    reviewer_id: uuid.UUID | None = None
    quality_notes: str | None = None
    disclosure_notes: str | None = None

    model_config = ConfigDict(extra="forbid")


class LocalizedSubtitlePackageRead(LocalizedSubtitlePackageCreate):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    google_drive_ctas: list[dict[str, Any]] = Field(default_factory=list)
    operator_summary: str
    created_at: AwareDatetime
    updated_at: AwareDatetime
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class LocalizedMetadataPackageCreate(BaseModel):
    language: str
    region: str | None = None
    localized_title: str
    localized_description: str
    localized_tags: list[str] = Field(default_factory=list)
    localized_disclosure_notes: str | None = None
    localized_cta_text: str | None = None
    human_review_status: LocalizedMetadataReviewStatus = "NEEDS_HUMAN_REVIEW"
    reviewer_id: uuid.UUID | None = None
    quality_notes: str | None = None

    model_config = ConfigDict(extra="forbid")


class LocalizedMetadataPackageRead(LocalizedMetadataPackageCreate):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    operator_summary: str
    created_at: AwareDatetime
    updated_at: AwareDatetime
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class VideoProjectLocalizationRead(BaseModel):
    video_project_id: uuid.UUID
    subtitle_packages: list[LocalizedSubtitlePackageRead]
    metadata_packages: list[LocalizedMetadataPackageRead]
    readiness: dict[str, Any]
    operator_summary: str

    model_config = ConfigDict(extra="forbid")


class LocalizationReadinessGateRead(BaseModel):
    video_project_id: uuid.UUID
    result: LocalizationReadinessResult
    missing_subtitle_languages: list[str] = Field(default_factory=list)
    missing_metadata_languages: list[str] = Field(default_factory=list)
    unreviewed_subtitle_languages: list[str] = Field(default_factory=list)
    unreviewed_metadata_languages: list[str] = Field(default_factory=list)
    disclosure_translation_status: str
    operator_summary: str
    next_action: str
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ChannelPublishTimingPolicyCreate(BaseModel):
    primary_timezone: str
    operator_timezone: str | None = None
    target_regions: list[str] = Field(default_factory=list)
    primary_audience_country: str | None = None
    preferred_publish_windows: list[dict[str, Any]] = Field(default_factory=list)
    avoid_publish_windows: list[dict[str, Any]] = Field(default_factory=list)
    publish_days: list[str] = Field(default_factory=list)
    weekend_allowed: bool = True
    notes: str | None = None

    model_config = ConfigDict(extra="forbid")


class ChannelPublishTimingPolicyRead(ChannelPublishTimingPolicyCreate):
    id: uuid.UUID
    channel_workspace_id: uuid.UUID
    operator_summary: str
    created_at: AwareDatetime
    updated_at: AwareDatetime
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class PublishTimingSuggestionRead(BaseModel):
    id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID | None
    publish_handoff_package_id: uuid.UUID | None
    target_timezone: str
    operator_timezone: str | None
    suggested_publish_at_local: AwareDatetime
    suggested_publish_at_utc: AwareDatetime
    operator_local_time: AwareDatetime | None
    source: PublishTimingSource
    confidence_label: PublishTimingConfidence
    operator_summary: str
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")
