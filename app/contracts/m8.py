import math
import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


AnalyticsPlatform = Literal["YOUTUBE", "YOUTUBE_SHORTS", "TIKTOK", "FACEBOOK", "INSTAGRAM", "GENERIC"]
AnalyticsSyncMode = Literal["MANUAL_IMPORT", "CSV_IMPORT", "REAL_DISABLED", "YOUTUBE_PUBLIC_MONITOR", "YOUTUBE_OWNER_ANALYTICS"]
AnalyticsSyncState = Literal["PENDING", "RUNNING", "COMPLETED", "BLOCKED", "FAILED", "CANCELLED"]
AnalyticsObservationWindow = Literal["T_PLUS_1H", "T_PLUS_6H", "T_PLUS_24H", "T_PLUS_48H", "T_PLUS_7D", "CUSTOM", "UNKNOWN"]
MetricGroup = Literal["REACH", "ENGAGEMENT", "RETENTION", "TRAFFIC", "AUDIENCE", "REVENUE_DISABLED", "OTHER"]
MetricUnit = Literal["COUNT", "PERCENT", "SECONDS", "MINUTES", "RATIO", "CURRENCY", "UNKNOWN"]
MetricDefinitionStatus = Literal["ACTIVE", "DISABLED", "DEPRECATED"]
MetricFreshnessState = Literal["FRESH", "STALE", "UNKNOWN", "NOT_AVAILABLE"]
MetricConfidenceLevel = Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]
UploadedVideoAnalyticsMonitoringState = Literal["READY_FOR_ANALYTICS", "SYNCED", "PARTIAL_DATA", "NO_DATA_YET", "STALE", "BLOCKED"]
MetricAvailabilityState = Literal["AVAILABLE", "UNKNOWN", "NOT_AVAILABLE"]


KNOWN_ANALYTICS_METRICS = {
    "views",
    "impressions",
    "click_through_rate",
    "average_view_duration_seconds",
    "average_view_percentage",
    "watch_time_minutes",
    "likes",
    "comments",
    "shares",
    "subscribers_gained",
    "subscribers_lost",
    "reach",
    "engagement_rate",
    "saves",
    "bookmarks",
    "completion_rate",
}


def _validate_metric_blob(value: dict[str, Any] | None) -> dict[str, float] | None:
    if value is None:
        return None
    result: dict[str, float] = {}
    for key, metric_value in value.items():
        if isinstance(metric_value, bool) or metric_value is None:
            raise ValueError(f"metric value for {key} must be numeric")
        numeric = float(metric_value)
        if not math.isfinite(numeric) or numeric < 0:
            raise ValueError(f"metric value for {key} must be finite and non-negative")
        result[key] = numeric
    return result


class MetricDefinitionVersionRead(BaseModel):
    id: uuid.UUID
    metric_key: str
    metric_name: str
    metric_group: MetricGroup
    platform: AnalyticsPlatform
    unit: MetricUnit
    description: str
    status: MetricDefinitionStatus
    version: str
    metadata: dict[str, Any]
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class AnalyticsSyncRunCreate(BaseModel):
    uploaded_video_id: uuid.UUID
    sync_mode: AnalyticsSyncMode = "YOUTUBE_OWNER_ANALYTICS"
    provider_key: str | None = None
    observed_from: AwareDatetime | None = None
    observed_to: AwareDatetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class AnalyticsSyncRunExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalyticsSyncRunRead(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    uploaded_video_id: uuid.UUID
    video_project_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    platform: AnalyticsPlatform
    platform_video_id: str
    sync_mode: AnalyticsSyncMode
    sync_state: AnalyticsSyncState
    started_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    observed_from: AwareDatetime | None
    observed_to: AwareDatetime | None
    provider_key: str | None
    provider_attempt_id: uuid.UUID | None
    analytics_snapshot_id: uuid.UUID | None
    reason_codes: list[str]
    next_action: str | None
    metadata: dict[str, Any]
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class MetricAvailabilityItem(BaseModel):
    state: MetricAvailabilityState
    source_metric_key: str | None = None
    reason_code: str | None = None
    unit: MetricUnit | None = None
    provider_key: str | None = None
    source: str | None = None

    model_config = ConfigDict(extra="forbid")


class TrafficSourceItem(BaseModel):
    source_key: str = Field(min_length=1)
    source_label: str = Field(min_length=1)
    views: float | None = None
    impressions: float | None = None
    watch_time_minutes: float | None = None
    percentage: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @field_validator("views", "impressions", "watch_time_minutes", "percentage")
    @classmethod
    def validate_nullable_numeric(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not math.isfinite(float(value)) or value < 0:
            raise ValueError("traffic source numeric values must be finite and non-negative")
        return value

    @field_validator("percentage")
    @classmethod
    def validate_percentage(cls, value: float | None) -> float | None:
        if value is not None and value > 100:
            raise ValueError("traffic source percentage must be between 0 and 100")
        return value


class RetentionCurvePoint(BaseModel):
    time_seconds: float = Field(ge=0)
    retention_percent: float | None = None
    viewers_remaining_estimate: float | None = None
    source_metric: str | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("retention_percent")
    @classmethod
    def validate_retention_percent(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if not math.isfinite(value) or value < 0 or value > 100:
            raise ValueError("retention_percent must be between 0 and 100")
        return value

    @field_validator("viewers_remaining_estimate")
    @classmethod
    def validate_viewers_remaining(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if not math.isfinite(value) or value < 0:
            raise ValueError("viewers_remaining_estimate must be finite and non-negative")
        return value


class AnalyticsProviderOutputContract(BaseModel):
    platform: AnalyticsPlatform
    platform_video_id: str = Field(min_length=1)
    captured_at: AwareDatetime
    observed_from: AwareDatetime | None = None
    observed_to: AwareDatetime | None = None
    observation_window: AnalyticsObservationWindow = "UNKNOWN"
    metrics: dict[str, float] = Field(default_factory=dict)
    metric_availability: dict[str, MetricAvailabilityItem] = Field(default_factory=dict)
    traffic_sources: list[TrafficSourceItem] | None = None
    retention_curve: list[RetentionCurvePoint] | None = None
    engagement: dict[str, float] | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    freshness_state: MetricFreshnessState
    confidence_level: MetricConfidenceLevel

    model_config = ConfigDict(extra="forbid")

    @field_validator("metrics", "engagement")
    @classmethod
    def validate_numeric_blob(cls, value: dict[str, Any] | None) -> dict[str, float] | None:
        return _validate_metric_blob(value)

    @model_validator(mode="after")
    def validate_observed_range(self) -> "AnalyticsProviderOutputContract":
        if self.observed_from and self.observed_to and self.observed_from > self.observed_to:
            raise ValueError("observed_from must be before observed_to")
        return self


class ManualAnalyticsImportContract(BaseModel):
    uploaded_video_id: uuid.UUID
    platform: AnalyticsPlatform
    platform_video_id: str = Field(min_length=1)
    captured_at: AwareDatetime
    observed_from: AwareDatetime | None = None
    observed_to: AwareDatetime | None = None
    observation_window: AnalyticsObservationWindow = "UNKNOWN"
    metrics: dict[str, float] = Field(default_factory=dict)
    traffic_sources: list[TrafficSourceItem] | None = None
    retention_curve: list[RetentionCurvePoint] | None = None
    engagement: dict[str, float] | None = None
    duration_seconds: float | None = None
    timeline_alignment: dict[str, Any] = Field(default_factory=dict)
    source_note: str | None = None
    imported_by_user_id: uuid.UUID | None = None
    strict_metric_keys: bool = True

    model_config = ConfigDict(extra="forbid")

    @field_validator("metrics", "engagement")
    @classmethod
    def validate_manual_numeric_blob(cls, value: dict[str, Any] | None) -> dict[str, float] | None:
        return _validate_metric_blob(value)

    @field_validator("duration_seconds")
    @classmethod
    def validate_duration(cls, value: float | None) -> float | None:
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError("duration_seconds must be finite and non-negative")
        return value

    @model_validator(mode="after")
    def validate_manual_import(self) -> "ManualAnalyticsImportContract":
        if self.observed_from and self.observed_to and self.observed_from > self.observed_to:
            raise ValueError("observed_from must be before observed_to")
        if self.strict_metric_keys:
            unknown = sorted(key for key in self.metrics if key not in KNOWN_ANALYTICS_METRICS)
            if unknown:
                raise ValueError(f"unknown metric keys: {', '.join(unknown)}")
        if self.duration_seconds is not None and self.retention_curve:
            out_of_range = [point.time_seconds for point in self.retention_curve if point.time_seconds > self.duration_seconds]
            if out_of_range:
                raise ValueError("retention time_seconds must be within duration_seconds")
        return self


class AnalyticsSnapshotRead(BaseModel):
    id: uuid.UUID
    analytics_sync_run_id: uuid.UUID
    uploaded_video_id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    platform: AnalyticsPlatform
    platform_video_id: str
    captured_at: AwareDatetime
    observed_from: AwareDatetime | None
    observed_to: AwareDatetime | None
    observation_window: AnalyticsObservationWindow
    metrics_blob: dict[str, Any]
    normalized_metrics_blob: dict[str, Any]
    metric_availability: dict[str, Any]
    source_metadata: dict[str, Any]
    freshness_state: MetricFreshnessState
    confidence_level: MetricConfidenceLevel
    reason_codes: list[str]
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class MetricAvailabilitySnapshotRead(BaseModel):
    id: uuid.UUID
    uploaded_video_id: uuid.UUID
    analytics_sync_run_id: uuid.UUID | None
    platform: AnalyticsPlatform
    platform_video_id: str
    availability_blob: dict[str, Any]
    unavailable_metrics: list[str]
    unknown_metrics: list[str]
    source_metric_keys: list[str]
    freshness_state: MetricFreshnessState
    confidence_level: MetricConfidenceLevel
    captured_at: AwareDatetime
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class TrafficSourceSnapshotRead(BaseModel):
    id: uuid.UUID
    analytics_snapshot_id: uuid.UUID
    uploaded_video_id: uuid.UUID
    platform: AnalyticsPlatform
    platform_video_id: str
    captured_at: AwareDatetime
    traffic_sources: list[dict[str, Any]]
    source_summary: dict[str, Any]
    freshness_state: MetricFreshnessState
    confidence_level: MetricConfidenceLevel
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class RetentionCurveSnapshotRead(BaseModel):
    id: uuid.UUID
    analytics_snapshot_id: uuid.UUID
    uploaded_video_id: uuid.UUID
    video_project_id: uuid.UUID
    render_package_snapshot_id: uuid.UUID | None
    platform: AnalyticsPlatform
    platform_video_id: str
    captured_at: AwareDatetime
    curve_points: list[dict[str, Any]]
    curve_summary: dict[str, Any]
    duration_seconds: float | None
    timeline_alignment: dict[str, Any]
    freshness_state: MetricFreshnessState
    confidence_level: MetricConfidenceLevel
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class EngagementSnapshotRead(BaseModel):
    id: uuid.UUID
    analytics_snapshot_id: uuid.UUID
    uploaded_video_id: uuid.UUID
    platform: AnalyticsPlatform
    platform_video_id: str
    captured_at: AwareDatetime
    engagement_blob: dict[str, Any]
    freshness_state: MetricFreshnessState
    confidence_level: MetricConfidenceLevel
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class UploadedVideoMetricsSummaryRead(BaseModel):
    id: uuid.UUID
    uploaded_video_id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    platform: AnalyticsPlatform
    platform_video_id: str
    latest_analytics_snapshot_id: uuid.UUID | None
    latest_retention_curve_snapshot_id: uuid.UUID | None
    latest_traffic_source_snapshot_id: uuid.UUID | None
    latest_engagement_snapshot_id: uuid.UUID | None
    latest_captured_at: AwareDatetime | None
    metrics_summary: dict[str, Any]
    availability_summary: dict[str, Any]
    freshness_state: MetricFreshnessState
    confidence_level: MetricConfidenceLevel
    monitoring_state: UploadedVideoAnalyticsMonitoringState
    operator_summary: str | None
    next_action: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")
