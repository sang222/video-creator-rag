import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


UploadDestination = Literal["YOUTUBE"]
HumanUploadTaskStatus = Literal[
    "READY_FOR_HUMAN_UPLOAD",
    "HUMAN_UPLOAD_IN_PROGRESS",
    "UPLOADED_WAITING_BACKFILL",
    "BACKFILLED_WAITING_VERIFICATION",
    "UPLOADED_VERIFIED",
    "UPLOADED_UNVERIFIED",
    "BLOCKED",
    "CANCELLED",
]
UploadedVideoVisibility = Literal["PUBLIC", "UNLISTED", "PRIVATE", "SCHEDULED", "UNKNOWN"]
UploadedVideoVerificationStatus = Literal[
    "NOT_VERIFIED",
    "VERIFIED_PUBLIC",
    "VERIFIED_OWNER",
    "VERIFICATION_UNAVAILABLE",
    "VERIFICATION_FAILED",
]
UploadedVideoAnalyticsSyncStatus = Literal["NOT_STARTED", "NOT_CONFIGURED", "PENDING", "SYNCED", "FAILED"]
BackfillParseStatus = Literal["PARSED", "INVALID", "DUPLICATE", "ERROR"]


class HumanUploadTaskLedgerRead(BaseModel):
    id: uuid.UUID
    channel_id: uuid.UUID
    video_project_id: uuid.UUID | None = None
    first_scripted_video_package_id: uuid.UUID | None = None
    publish_package_id: uuid.UUID | None = None
    destination: UploadDestination = "YOUTUBE"
    status: HumanUploadTaskStatus
    upload_card_ref: str | None = None
    title_snapshot: str
    description_snapshot: str | None = None
    thumbnail_ref: Any | None = None
    subtitle_refs: list[dict[str, Any]] = Field(default_factory=list)
    required_assets: list[dict[str, Any]] = Field(default_factory=list)
    checklist: list[dict[str, Any]] = Field(default_factory=list)
    actual_uploaded_video_id: uuid.UUID | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    blocked_reason: str | None = None
    operator_note: str | None = None
    next_action: str

    model_config = ConfigDict(extra="forbid")


class HumanUploadTaskListRead(BaseModel):
    channel_id: uuid.UUID
    tasks: list[HumanUploadTaskLedgerRead]
    need_upload_count: int
    waiting_backfill_count: int
    uploaded_count: int
    waiting_verification_count: int
    verified_count: int
    analytics_not_configured_count: int
    unverified_count: int
    blocked_count: int

    model_config = ConfigDict(extra="forbid")


class BackfillUploadedVideoRequest(BaseModel):
    youtube_url_or_video_id: str = Field(min_length=1)
    actual_title: str | None = None
    actual_visibility: UploadedVideoVisibility | None = None
    actual_publish_time: AwareDatetime | None = None
    actual_upload_time: AwareDatetime | None = None
    playlist_id: str | None = None
    thumbnail_uploaded: bool | None = None
    subtitles_uploaded: bool | None = None
    description_modified_from_package: bool | None = None
    operator_note: str | None = None

    model_config = ConfigDict(extra="forbid")


class UploadedVideoBackfillEventRead(BaseModel):
    id: uuid.UUID
    uploaded_video_id: uuid.UUID | None = None
    human_upload_task_id: uuid.UUID | None = None
    channel_id: uuid.UUID
    input_url_or_video_id: str
    parsed_video_id: str | None = None
    parse_status: BackfillParseStatus
    previous_status: str | None = None
    new_status: str | None = None
    operator_note: str | None = None
    created_by: uuid.UUID | None = None
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class UploadedVideoLedgerRead(BaseModel):
    id: uuid.UUID
    channel_id: uuid.UUID
    video_project_id: uuid.UUID | None = None
    first_scripted_video_package_id: uuid.UUID | None = None
    publish_package_id: uuid.UUID | None = None
    human_upload_task_id: uuid.UUID | None = None
    destination: UploadDestination = "YOUTUBE"
    external_video_id: str
    external_url: str
    actual_title: str | None = None
    actual_visibility: UploadedVideoVisibility = "UNKNOWN"
    actual_publish_time: AwareDatetime | None = None
    actual_upload_time: AwareDatetime | None = None
    playlist_id: str | None = None
    thumbnail_uploaded: bool | None = None
    subtitles_uploaded: bool | None = None
    description_modified_from_package: bool | None = None
    package_metadata_diff: dict[str, Any] | None = None
    verification_status: UploadedVideoVerificationStatus = "NOT_VERIFIED"
    analytics_sync_status: UploadedVideoAnalyticsSyncStatus = "NOT_STARTED"
    last_verified_at: AwareDatetime | None = None
    last_analytics_sync_at: AwareDatetime | None = None
    operator_note: str | None = None
    next_action: str
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class UploadedVideoListRead(BaseModel):
    channel_id: uuid.UUID
    uploaded_videos: list[UploadedVideoLedgerRead]

    model_config = ConfigDict(extra="forbid")


class BackfillUploadedVideoResult(BaseModel):
    task: HumanUploadTaskLedgerRead
    uploaded_video: UploadedVideoLedgerRead
    backfill_event: UploadedVideoBackfillEventRead
    parsed_video_id: str
    next_action: str

    model_config = ConfigDict(extra="forbid")


class UploadedVideoVerificationResult(BaseModel):
    uploaded_video: UploadedVideoLedgerRead
    verification_status: UploadedVideoVerificationStatus
    analytics_sync_status: UploadedVideoAnalyticsSyncStatus
    next_action: str
    reason_codes: list[str] = Field(default_factory=list)
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class PublishLedgerRead(BaseModel):
    channel_id: uuid.UUID
    need_upload_count: int
    waiting_backfill_count: int
    uploaded_count: int
    waiting_verification_count: int
    verified_count: int
    analytics_not_configured_count: int
    unverified_count: int
    blocked_count: int
    latest_tasks: list[HumanUploadTaskLedgerRead]
    latest_uploaded_videos: list[UploadedVideoLedgerRead]
    operator_summary_vi: str

    model_config = ConfigDict(extra="forbid")
