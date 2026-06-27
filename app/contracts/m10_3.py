import uuid
from datetime import date
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


YouTubeAuthMode = Literal["API_KEY", "OAUTH2"]
YouTubeProviderKey = Literal["YOUTUBE_DATA_API", "YOUTUBE_ANALYTICS_API"]
YouTubeConnectionState = Literal["NOT_CONFIGURED", "CONFIGURED", "CONNECTED", "NEEDS_REAUTH", "REVOKED", "ERROR"]
YouTubeOAuthSessionStatus = Literal["STARTED", "CALLBACK_RECEIVED", "TOKEN_EXCHANGED", "FAILED", "CANCELLED"]
YouTubePublicSyncRunState = Literal["PENDING", "RUNNING", "COMPLETED", "FAILED", "SKIPPED"]
YouTubeOwnerSyncRunState = Literal["PENDING", "RUNNING", "COMPLETED", "FAILED", "SKIPPED", "NEEDS_AUTH"]
YouTubeSyncSource = Literal["YOUTUBE_DATA_API", "YOUTUBE_ANALYTICS_API"]
YouTubeMetricAvailability = Literal["AVAILABLE", "UNKNOWN", "NOT_AVAILABLE"]
YouTubeFollowFreshnessState = Literal["FRESH", "STALE", "UNKNOWN"]
YouTubePublicSyncStatus = Literal["OK", "FAILED", "PARTIAL", "NOT_CONFIGURED", "NOT_FOUND", "UNAVAILABLE"]
YouTubeOwnerSyncStatus = Literal["OK", "FAILED", "PARTIAL", "NEEDS_AUTH", "NOT_CONFIGURED", "NOT_FOUND", "UNAVAILABLE"]
YouTubeLearningAuthority = Literal["WEAK", "STRONG", "MIXED", "NONE"]
YouTubeTitleMatchStatus = Literal["OK", "CHANGED", "UNKNOWN"]
YouTubeDurationMatchStatus = Literal["OK", "REVIEW", "UNKNOWN"]
YouTubeCaptionStatus = Literal["AVAILABLE", "UNKNOWN", "NOT_AVAILABLE"]
YouTubeVisibilityStatus = Literal["PUBLIC", "UNLISTED", "PRIVATE", "UNKNOWN"]


PUBLIC_MONITOR_METRICS = ("views", "likes", "comments")
OWNER_ANALYTICS_METRICS = (
    "views",
    "likes",
    "comments",
    "impressions",
    "impression_click_through_rate",
    "average_view_duration_seconds",
    "average_view_percentage",
    "estimated_minutes_watched",
    "subscribers_gained",
    "subscribers_lost",
)


class YouTubeMonitoringCredentialRead(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID | None
    channel_workspace_id: uuid.UUID | None
    credential_reference_id: uuid.UUID
    auth_mode: YouTubeAuthMode
    provider_key: YouTubeProviderKey
    connection_state: YouTubeConnectionState
    scopes: list[str]
    token_metadata: dict[str, Any]
    last_health_check_at: AwareDatetime | None
    error_code: str | None
    error_message: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class YouTubeOAuthSessionRead(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID | None
    channel_workspace_id: uuid.UUID | None
    redirect_uri: str
    scopes: list[str]
    status: YouTubeOAuthSessionStatus
    credential_reference_id: uuid.UUID | None
    error_code: str | None
    error_message: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class YouTubeOAuthStartResult(BaseModel):
    oauth_session_id: uuid.UUID
    authorization_url: str

    model_config = ConfigDict(extra="forbid")


class YouTubeConnectionStatusRead(BaseModel):
    public_monitor_enabled: bool
    public_config_state: YouTubeConnectionState
    owner_analytics_enabled: bool
    owner_connection_state: YouTubeConnectionState
    owner_analytics_connected: bool
    public_credential_reference_id: uuid.UUID | None = None
    owner_credential_reference_id: uuid.UUID | None = None
    scopes: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    next_action: str | None = None

    model_config = ConfigDict(extra="forbid")


class YouTubePublicSyncRunRead(BaseModel):
    id: uuid.UUID
    uploaded_video_id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID | None
    platform_video_id: str
    run_state: YouTubePublicSyncRunState
    source: Literal["YOUTUBE_DATA_API"]
    started_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    http_status: int | None
    error_code: str | None
    error_message: str | None
    metrics_found: bool
    created_snapshot_id: uuid.UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class YouTubeOwnerAnalyticsSyncRequest(BaseModel):
    start_date: date | None = None
    end_date: date | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_range(self) -> "YouTubeOwnerAnalyticsSyncRequest":
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        return self


class YouTubeOwnerAnalyticsSyncRunRead(BaseModel):
    id: uuid.UUID
    uploaded_video_id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID | None
    platform_video_id: str
    credential_reference_id: uuid.UUID | None
    run_state: YouTubeOwnerSyncRunState
    source: Literal["YOUTUBE_ANALYTICS_API"]
    start_date: date
    end_date: date
    started_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    http_status: int | None
    error_code: str | None
    error_message: str | None
    metrics_found: bool
    created_snapshot_id: uuid.UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class YouTubePublicMonitorSnapshotRead(BaseModel):
    id: uuid.UUID
    uploaded_video_id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID | None
    platform_video_id: str
    video_url: str | None
    views: int | None
    likes: int | None
    comments: int | None
    youtube_title: str | None
    youtube_published_at: AwareDatetime | None
    youtube_channel_id: str | None
    youtube_channel_title: str | None
    thumbnail_url: str | None
    duration_seconds: int | None
    definition: str | None
    caption_status: str | None
    privacy_status: str | None
    public_stats_viewable: bool | None
    title_matches_confirmed_metadata: bool | None
    duration_matches_render_package: bool | None
    views_availability: YouTubeMetricAvailability
    likes_availability: YouTubeMetricAvailability
    comments_availability: YouTubeMetricAvailability
    freshness_state: YouTubeFollowFreshnessState
    sync_status: YouTubePublicSyncStatus
    sync_error_code: str | None
    learning_authority: Literal["WEAK"]
    last_synced_at: AwareDatetime
    unknown_metrics: list[str]
    unavailable_metrics: list[str]
    technical_appendix: dict[str, Any]
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class YouTubeOwnerAnalyticsSnapshotRead(BaseModel):
    id: uuid.UUID
    uploaded_video_id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID | None
    platform_video_id: str
    analytics_start_date: date
    analytics_end_date: date
    learning_authority: Literal["STRONG"]
    views: int | None
    likes: int | None
    comments: int | None
    impressions: int | None
    impression_click_through_rate: float | None
    average_view_duration_seconds: float | None
    average_view_percentage: float | None
    estimated_minutes_watched: float | None
    subscribers_gained: int | None
    subscribers_lost: int | None
    metric_availability: dict[str, Any]
    freshness_state: YouTubeFollowFreshnessState
    sync_status: YouTubeOwnerSyncStatus
    sync_error_code: str | None
    last_synced_at: AwareDatetime
    technical_appendix: dict[str, Any]
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class YouTubePublicProviderOutput(BaseModel):
    platform_video_id: str = Field(min_length=1)
    video_url: str | None = None
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    youtube_title: str | None = None
    youtube_published_at: AwareDatetime | None = None
    youtube_channel_id: str | None = None
    youtube_channel_title: str | None = None
    thumbnail_url: HttpUrl | str | None = None
    duration_seconds: int | None = Field(default=None, ge=0)
    definition: str | None = None
    caption_status: str | None = None
    privacy_status: str | None = None
    public_stats_viewable: bool | None = None
    metric_availability: dict[str, YouTubeMetricAvailability] = Field(default_factory=dict)
    freshness_state: YouTubeFollowFreshnessState = "FRESH"
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @field_validator("views", "likes", "comments")
    @classmethod
    def validate_counts(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("YouTube public counts must be non-negative")
        return value


class YouTubeOwnerAnalyticsProviderOutput(BaseModel):
    platform_video_id: str = Field(min_length=1)
    analytics_start_date: date
    analytics_end_date: date
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    impressions: int | None = None
    impression_click_through_rate: float | None = None
    average_view_duration_seconds: float | None = None
    average_view_percentage: float | None = None
    estimated_minutes_watched: float | None = None
    subscribers_gained: int | None = None
    subscribers_lost: int | None = None
    metric_availability: dict[str, YouTubeMetricAvailability] = Field(default_factory=dict)
    freshness_state: YouTubeFollowFreshnessState = "FRESH"
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_output(self) -> "YouTubeOwnerAnalyticsProviderOutput":
        if self.analytics_start_date > self.analytics_end_date:
            raise ValueError("analytics_start_date must be on or before analytics_end_date")
        for key in OWNER_ANALYTICS_METRICS:
            value = getattr(self, key)
            if value is not None and value < 0:
                raise ValueError(f"{key} must be non-negative")
        return self


class UploadedVideoYouTubeFollowSummaryRead(BaseModel):
    uploaded_video_id: uuid.UUID
    platform_video_id: str
    video_url: str
    title: str | None
    thumbnail_url: str | None
    published_at: AwareDatetime | None
    views: int | None
    likes: int | None
    comments: int | None
    public_last_synced_at: AwareDatetime | None
    public_freshness_state: YouTubeFollowFreshnessState
    owner_analytics_connected: bool
    impressions: int | None
    impression_click_through_rate: float | None
    average_view_duration_seconds: float | None
    average_view_percentage: float | None
    estimated_minutes_watched: float | None
    subscribers_gained: int | None
    subscribers_lost: int | None
    owner_last_synced_at: AwareDatetime | None
    owner_freshness_state: YouTubeFollowFreshnessState
    title_match_status: YouTubeTitleMatchStatus
    duration_match_status: YouTubeDurationMatchStatus
    caption_status: YouTubeCaptionStatus
    visibility_status: YouTubeVisibilityStatus
    learning_authority: YouTubeLearningAuthority
    unavailable_metrics: list[str] = Field(default_factory=list)
    unknown_metrics: list[str] = Field(default_factory=list)
    next_action: str | None = None
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")
