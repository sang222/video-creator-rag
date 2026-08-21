"""Strict contracts for private YouTube staging and operator delivery."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

SHA256_PATTERN = r"^[0-9a-f]{64}$"


class YouTubePublishingCapability(StrEnum):
    VIDEO_UPLOAD_PRIVATE = "VIDEO_UPLOAD_PRIVATE"
    THUMBNAIL_WRITE = "THUMBNAIL_WRITE"
    CAPTION_WRITE = "CAPTION_WRITE"
    METADATA_READBACK = "METADATA_READBACK"
    PROCESSING_READBACK = "PROCESSING_READBACK"
    PLAYLIST_WRITE = "PLAYLIST_WRITE"


class YouTubePublishingCredentialCreate(BaseModel):
    credential_reference_id: uuid.UUID
    platform_channel_id: str = Field(min_length=1)
    account_identity: str = Field(min_length=1)
    oauth_scopes: list[str] = Field(min_length=1)
    capabilities: list[YouTubePublishingCapability] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class YouTubePublishingCredentialRead(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    credential_reference_id: uuid.UUID
    platform_channel_id: str
    account_identity: str
    oauth_scopes: list[str]
    capabilities: list[str]
    public_release_allowed: Literal[False]
    delete_allowed: Literal[False]
    state: str
    content_hash: str
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ProductionThumbnailBindingCreate(BaseModel):
    thumbnail_variant_id: uuid.UUID | None = None
    provider_key: str = Field(min_length=1)
    provider_effect_ref: str = Field(min_length=1)
    provider_effect_hash: str = Field(pattern=SHA256_PATTERN)
    file_ref: str = Field(min_length=1)
    checksum_sha256: str = Field(pattern=SHA256_PATTERN)
    mime_type: Literal["image/jpeg", "image/png"]
    size_bytes: int = Field(gt=0, le=2_097_152)
    width: int = Field(gt=0)
    height: int = Field(gt=0)

    model_config = ConfigDict(extra="forbid")


class ProductionThumbnailBindingRead(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    final_review_candidate_id: uuid.UUID
    thumbnail_variant_id: uuid.UUID | None
    source_type: Literal["AI_GENERATED"]
    provider_key: str
    provider_effect_ref: str
    provider_effect_hash: str
    file_ref: str
    checksum_sha256: str
    mime_type: str
    size_bytes: int
    width: int
    height: int
    state: Literal["VERIFIED"]
    content_hash: str
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class YouTubePrivateStagePrepare(BaseModel):
    publishing_credential_id: uuid.UUID
    production_thumbnail_binding_id: uuid.UUID
    caption_ref: str = Field(min_length=1)
    caption_hash: str = Field(pattern=SHA256_PATTERN)
    tags: list[str] = Field(default_factory=list, max_length=500)
    category_id: str = Field(default="28", min_length=1)
    default_language: str | None = Field(default=None, min_length=2)
    made_for_kids: bool = False
    contains_synthetic_media: bool

    model_config = ConfigDict(extra="forbid")


class YouTubePrivateStageRead(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    final_review_candidate_id: uuid.UUID
    final_video_decision_id: uuid.UUID | None
    final_media_ref_id: uuid.UUID
    final_media_ref: str
    final_media_checksum: str
    publishing_credential_id: uuid.UUID
    production_thumbnail_binding_id: uuid.UUID
    caption_ref: str
    caption_hash: str
    staging_metadata: dict[str, Any]
    staging_metadata_hash: str
    public_release_expectation: dict[str, Any]
    public_release_expectation_hash: str
    state: str
    platform_video_id: str | None
    studio_url: str | None
    observed_metadata: dict[str, Any] | None
    observed_metadata_hash: str | None
    processing_status: str | None
    private_verified_at: AwareDatetime | None
    last_error_code: str | None
    identity_hash: str
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class YouTubeStageExecute(BaseModel):
    command_id: uuid.UUID

    model_config = ConfigDict(extra="forbid")


class YouTubePrivateStageReview(BaseModel):
    disposition: Literal["REJECT", "NEEDS_RERENDER"]
    reason: str = Field(min_length=1, max_length=4000)

    model_config = ConfigDict(extra="forbid")


class YouTubePrivateReadback(BaseModel):
    platform_channel_id: str = Field(min_length=1)
    platform_video_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str
    tags: list[str] = Field(default_factory=list)
    category_id: str = Field(min_length=1)
    default_language: str | None = None
    privacy_status: Literal["PRIVATE"]
    processing_status: Literal["SUCCEEDED"]
    made_for_kids: bool
    self_declared_made_for_kids: bool | None = None
    contains_synthetic_media: bool
    thumbnail_verified: bool
    caption_verified: bool
    evidence_ref: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class PublicPublicationReceiptRead(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    final_review_candidate_id: uuid.UUID
    final_video_decision_id: uuid.UUID | None
    manual_publish_confirmation_id: uuid.UUID | None
    youtube_private_stage_id: uuid.UUID | None
    platform_channel_id: str
    platform_video_id: str
    public_url: str
    observed_privacy_status: Literal["PUBLIC"]
    observed_published_at: AwareDatetime
    observed_metadata: dict[str, Any]
    observed_metadata_hash: str
    verification_evidence_ref: str
    verification_evidence_hash: str
    receipt_hash: str
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class LocalMediaPurgeRequest(BaseModel):
    command_id: uuid.UUID

    model_config = ConfigDict(extra="forbid")


class LocalMediaPurgeReceiptRead(BaseModel):
    id: uuid.UUID
    youtube_private_stage_id: uuid.UUID
    final_media_ref_id: uuid.UUID
    local_file_ref: str
    checksum_sha256: str
    state: Literal["PURGED"]
    deleted_at: AwareDatetime
    receipt_hash: str
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class TelegramNotificationRead(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    final_review_candidate_id: uuid.UUID
    credential_reference_id: uuid.UUID | None
    chat_binding_ref: str | None
    notification_kind: str
    payload: dict[str, Any]
    payload_hash: str
    state: str
    attempt_count: int
    provider_message_id: str | None
    provider_response_hash: str | None
    error_code: str | None
    sent_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class YouTubeSeriesPlaylistBindingCreate(BaseModel):
    publishing_credential_id: uuid.UUID
    expected_title: str = Field(min_length=1)
    expected_description: str = ""

    model_config = ConfigDict(extra="forbid")


class YouTubeSeriesPlaylistBindingRead(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    series_plan_id: uuid.UUID
    publishing_credential_id: uuid.UUID
    platform_channel_id: str
    youtube_playlist_id: str | None
    state: str
    expected_metadata: dict[str, Any]
    observed_metadata: dict[str, Any] | None
    last_verified_at: AwareDatetime | None
    binding_hash: str
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class YouTubeSeriesEpisodeBindingRead(BaseModel):
    id: uuid.UUID
    youtube_series_playlist_binding_id: uuid.UUID
    series_plan_id: uuid.UUID
    series_run_id: uuid.UUID
    technical_episode_number: int
    public_episode_ordinal: int | None
    public_ordinal_authority_ref: str | None
    public_ordinal_authority_hash: str | None
    video_project_id: uuid.UUID
    youtube_private_stage_id: uuid.UUID
    public_publication_receipt_id: uuid.UUID | None
    youtube_video_id: str
    youtube_playlist_item_id: str | None
    state: str
    expected_position: int | None
    actual_position: int | None
    last_verified_at: AwareDatetime | None
    binding_hash: str
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class YouTubeSeriesOrdinalBind(BaseModel):
    public_episode_ordinal: int = Field(gt=0)
    public_ordinal_authority_ref: str = Field(min_length=1)
    public_ordinal_authority_hash: str = Field(pattern=SHA256_PATTERN)

    model_config = ConfigDict(extra="forbid")


class TelegramSendResult(BaseModel):
    message_id: str = Field(min_length=1)
    response_payload: dict[str, Any]

    model_config = ConfigDict(extra="forbid")


class ResumableSessionResult(BaseModel):
    session_uri: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class ResumableUploadStatus(BaseModel):
    state: Literal["INCOMPLETE", "COMPLETE", "FAILED"]
    committed_bytes: int = Field(ge=0)
    platform_video_id: str | None = None
    response_payload: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def complete_requires_video_id(self) -> "ResumableUploadStatus":
        if self.state == "COMPLETE" and not self.platform_video_id:
            raise ValueError("complete upload requires platform_video_id")
        return self
